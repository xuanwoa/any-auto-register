from __future__ import annotations
import re
import time
import imaplib
import email
from email.header import decode_header
import urllib.parse
from core.base_mailbox import BaseMailbox, MailboxAccount, _extract_verification_link

def _get_caller_platform() -> str:
    import inspect
    frame = inspect.currentframe()
    while frame:
        if 'self' in frame.f_locals:
            obj = frame.f_locals['self']
            from core.base_platform import BasePlatform
            if isinstance(obj, BasePlatform):
                return getattr(obj, "name", "")
        frame = frame.f_back
    return ""


class IcloudMailbox(BaseMailbox):
    """iCloud Mail via Apple's official IMAP endpoint."""

    def __init__(self, username: str, password: str, aliases: str):
        self._username = username
        self._password = password
        # deduplicate using set, but keep order roughly (we'll shuffle later anyway)
        raw_aliases = [a.strip() for a in aliases.replace(',', '\n').split('\n') if a.strip()]
        self._aliases = list(dict.fromkeys(raw_aliases))
        self.imap_server = "imap.mail.me.com"
        self.imap_port = 993

    def _connect_imap(self) -> imaplib.IMAP4_SSL:
        try:
            print(f"[iCloudMailbox Debug] 正在连接 IMAP 服务器 {self.imap_server}:{self.imap_port} ...")
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            print(f"[iCloudMailbox Debug] 正在登录账号 {self._username} ...")
            mail.login(self._username, self._password)
            print(f"[iCloudMailbox Debug] 登录成功！选择 inbox 目录...")
            mail.select("inbox")
            return mail
        except Exception as e:
            print(f"[iCloudMailbox Debug] IMAP 登录惨遭失败: {e}")
            raise RuntimeError(f"iCloud IMAP 登录失败: {e}")

    def get_email(self) -> MailboxAccount:
        from sqlmodel import Session, select, func
        from core.db import engine, AccountModel

        if not self._aliases:
            raise ValueError("iCloud邮箱别名列表为空，请在设置中配置 iCloud Aliases")

        platform_name = _get_caller_platform()

        with Session(engine) as session:
            if platform_name:
                used_in_platform = session.exec(
                    select(AccountModel.email).where(AccountModel.platform == platform_name)
                ).all()
                used_in_platform_set = set(used_in_platform)
            else:
                used_in_platform_set = set()

            available_aliases = [a for a in self._aliases if a not in used_in_platform_set]
            if not available_aliases:
                raise ValueError(f"提供的所有 iCloud 别名均已在当前平台 ({platform_name or '全部'}) 注册过了，请在配置中追加新的别名。")

            counts = dict(session.exec(
                select(AccountModel.email, func.count('*'))
                .where(AccountModel.email.in_(available_aliases))
                .group_by(AccountModel.email)
            ).all())

        import random
        random.shuffle(available_aliases)
        available_aliases.sort(key=lambda a: counts.get(a, 0))
        chosen = available_aliases[0]

        return MailboxAccount(
            email=chosen,
            account_id=chosen,
            extra={
                "provider_resource": {
                    "provider_type": "mailbox",
                    "provider_name": "icloud_api",
                    "resource_type": "mailbox",
                    "resource_identifier": chosen,
                    "handle": chosen,
                    "display_name": chosen,
                    "metadata": {
                        "email": chosen,
                        "global_usage_count": counts.get(chosen, 0),
                    },
                },
            },
        )

    def get_current_ids(self, account: MailboxAccount) -> set:
        try:
            ids = set()
            mail = self._connect_imap()
            # To be safe against Hide My Email aliases, get ALL emails in INBOX and Junk
            for folder in ["INBOX", "Junk"]:
                try:
                    mail.select(folder)
                    status, messages = mail.search(None, 'ALL')
                    if status == 'OK' and messages[0]:
                        for eid in messages[0].split():
                            ids.add(f"{folder}:{eid.decode() if isinstance(eid, bytes) else str(eid)}")
                except Exception:
                    pass
            mail.logout()
            return ids
        except Exception as e:
            print(f"[iCloudMailbox] 获取最新ID失败: {e}")
        return set()

    def _get_email_body(self, msg) -> str:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype in ("text/plain", "text/html"):
                    try:
                        charset = part.get_content_charset()
                        part_body = part.get_payload(decode=True)
                        if part_body:
                            body += part_body.decode(charset or 'utf-8', errors='ignore') + " "
                    except Exception:
                        pass
        else:
            try:
                charset = msg.get_content_charset()
                part_body = msg.get_payload(decode=True)
                if part_body:
                    body = part_body.decode(charset or 'utf-8', errors='ignore')
            except Exception:
                pass
        return body

    def _poll_emails(self, account: MailboxAccount, keyword: str, before_ids: set, timeout: int, mode: str, code_pattern: str = None) -> str:
        start_time = time.time()
        seen = set(before_ids or set())
        last_mail = None
        folders = ["INBOX", "Junk"]
        
        while time.time() - start_time < timeout:
            try:
                if not last_mail:
                    mail = self._connect_imap()
                    last_mail = mail
                else:
                    mail = last_mail
                    try:
                        mail.noop()
                    except Exception:
                        mail = self._connect_imap()
                        last_mail = mail

                for folder in folders:
                    try:
                        mail.select(folder)
                    except Exception as e:
                        print(f"[iCloudMailbox Debug] 无法选择目录 {folder}: {e}")
                        continue
                    
                    # Since iCloud Hide My Email aliases might not strictly have the alias in the TO header 
                    # due to Apple's forwarding rewrite, we search all recent messages.
                    print(f"[iCloudMailbox Debug] 正在检索 {folder} 目录中的所有邮件...")
                    status, messages = mail.search(None, 'ALL')
                    if status != 'OK':
                        print(f"[iCloudMailbox Debug] IMAP 检索失败，状态码: {status}")
                        continue
                        
                    if not messages[0]:
                        print(f"[iCloudMailbox Debug] {folder} 目录为空或未找到邮件。")
                        continue
                        
                    email_ids = messages[0].split()
                    print(f"[iCloudMailbox Debug] 在 {folder} 目录中找到了 {len(email_ids)} 封信。准备检查最近的10封...")
                    # Only process the most recent 10 emails per folder to save time
                    for eid in reversed(email_ids[-10:]):
                        # Incorporate folder into seen key to avoid collision across folders if IMAP uses same sequence numbers
                            seen_key = f"{folder}:{eid.decode() if isinstance(eid, bytes) else str(eid)}"
                            print(f"[iCloudMailbox Debug] 检查邮件 eid={eid}, seen_key={seen_key}. 当前 seen 大小: {len(seen)}")
                            if seen_key in seen:
                                print(f"[iCloudMailbox Debug] 邮件 {seen_key} 已在 seen 中，跳过。")
                                continue
                            seen.add(seen_key)
                            
                            print(f"[iCloudMailbox Debug] 发起 fetch 提取 eid={eid} ...")

                            res, msg_data = mail.fetch(eid, '(BODY.PEEK[])')
                            print(f"[iCloudMailbox Debug] fetch 返回状态: {res}")
                            if res == 'OK':
                                try:
                                    raw_email = None
                                    for response_part in msg_data:
                                        if isinstance(response_part, tuple):
                                            raw_email = response_part[1]
                                            break
                                            
                                    if not raw_email:
                                        print(f"[iCloudMailbox Debug] 警告！无法从 eid={eid} 的 msg_data 中提取 tuple！msg_data: {msg_data}")
                                        continue
                                        
                                    msg = email.message_from_bytes(raw_email)
                                    
                                    subject = ""
                                    msg_subject = msg.get("Subject")
                                    if msg_subject:
                                        decoded_parts = decode_header(msg_subject)
                                        for part, charset in decoded_parts:
                                            if isinstance(part, bytes):
                                                subject += part.decode(charset or 'utf-8', errors='ignore')
                                            else:
                                                subject += str(part)

                                    body = self._get_email_body(msg)
                                    text = subject + " " + body
                                    
                                    print(f"[iCloudMailbox Debug] 分析邮件 (Folder: {folder}) | Subject: {subject} | 等待的关键词: '{keyword}'")

                                    print(f"[iCloudMailbox Debug] 分析新到的邮件 (Folder: {folder}) | Subject: {subject}")
                                    
                                    if keyword and keyword.lower() not in text.lower():
                                        print(f"[iCloudMailbox Debug] 邮件不包含关键词 '{keyword}' (正文长度:{len(text)})，跳过。")
                                        continue

                                    if mode == "code":
                                        m = re.search(code_pattern or r'(?<!#)(?<!\d)(\d{6})(?!\d)', text)
                                        if m:
                                            code_result = m.group(1) if m.groups() else m.group(0)
                                            print(f"[iCloudMailbox Debug] 成功提取到验证码: {code_result}")
                                            return code_result
                                        else:
                                            print(f"[iCloudMailbox Debug] 正则 {code_pattern} 匹配失败！邮件内容片段: {text[:200]}...")
                                    elif mode == "link":
                                        link = _extract_verification_link(text, keyword)
                                        if link:
                                            return link
                                except Exception as inner_e:
                                    import traceback
                                    print(f"[iCloudMailbox] 处理单只邮件 {eid} 时出错: {inner_e}, traceback:\n{traceback.format_exc()}")

            except Exception as e:
                last_mail = None
                print(f"[iCloudMailbox] poll 发生异常: {e}")

            time.sleep(4)

        raise TimeoutError(f"等待验证{'码' if mode == 'code' else '链接'}超时 ({timeout}s)")

    def wait_for_code(self, account: MailboxAccount, keyword: str = "",
                      timeout: int = 120, before_ids: set = None, code_pattern: str = None) -> str:
        return self._poll_emails(account, keyword, before_ids, timeout, "code", code_pattern)

    def wait_for_link(self, account: MailboxAccount, keyword: str = "",
                      timeout: int = 120, before_ids: set = None) -> str:
        return self._poll_emails(account, keyword, before_ids, timeout, "link", None)


def _create_icloud(extra: dict, proxy: str | None) -> 'BaseMailbox':
    return IcloudMailbox(
        username=extra.get("icloud_imap_username", ""),
        password=extra.get("icloud_app_password", ""),
        aliases=extra.get("icloud_aliases", ""),
    )

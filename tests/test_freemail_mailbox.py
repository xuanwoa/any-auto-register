import unittest
from unittest import mock

from core.base_mailbox import MailboxAccount, create_mailbox


class FreemailMailboxTests(unittest.TestCase):
    def _build_mailbox(self):
        mailbox = create_mailbox(
            "freemail",
            extra={"freemail_api_url": "https://freemail.example"},
        )
        mailbox._session = mock.Mock()
        return mailbox

    @mock.patch("time.sleep", return_value=None)
    def test_wait_for_code_skips_excluded_verification_code_field(self, _sleep):
        mailbox = self._build_mailbox()
        mailbox._session.get.side_effect = [
            _response(
                [
                    {"id": "m1", "verification_code": "111111"},
                ]
            ),
            _response(
                [
                    {"id": "m1", "verification_code": "111111"},
                    {"id": "m2", "verification_code": "222222"},
                ]
            ),
        ]

        code = mailbox.wait_for_code(
            MailboxAccount(email="demo@example.com"),
            timeout=5,
            exclude_codes={"111111"},
        )

        self.assertEqual(code, "222222")
        self.assertEqual(mailbox._session.get.call_count, 2)

    @mock.patch("time.sleep", return_value=None)
    def test_wait_for_code_skips_excluded_preview_extracted_code(self, _sleep):
        mailbox = self._build_mailbox()
        mailbox._session.get.side_effect = [
            _response(
                [
                    {"id": "m1", "verification_code": None, "preview": "Your verification code is 111111"},
                ]
            ),
            _response(
                [
                    {"id": "m1", "verification_code": None, "preview": "Your verification code is 111111"},
                    {"id": "m2", "verification_code": None, "preview": "Your verification code is 222222"},
                ]
            ),
        ]

        code = mailbox.wait_for_code(
            MailboxAccount(email="demo@example.com"),
            timeout=5,
            exclude_codes={"111111"},
        )

        self.assertEqual(code, "222222")
        self.assertEqual(mailbox._session.get.call_count, 2)

    def test_get_email_prefers_configured_domain_index(self):
        mailbox = create_mailbox(
            "freemail",
            extra={
                "freemail_api_url": "https://freemail.example",
                "freemail_domain": "target.example",
            },
        )
        mailbox._session = mock.Mock()
        mailbox._session.get.side_effect = [
            _response(["fallback.example", "target.example"]),
            _response({"email": "demo@target.example"}),
        ]

        account = mailbox.get_email()

        self.assertEqual(account.email, "demo@target.example")
        self.assertEqual(mailbox._session.get.call_count, 2)
        _, kwargs = mailbox._session.get.call_args
        self.assertEqual(kwargs.get("params"), {"domainIndex": 1})

    def test_get_email_without_domain_does_not_pass_domain_index(self):
        mailbox = create_mailbox(
            "freemail",
            extra={
                "freemail_api_url": "https://freemail.example",
            },
        )
        mailbox._session = mock.Mock()
        mailbox._session.get.return_value = _response({"email": "demo@random.example"})

        account = mailbox.get_email()

        self.assertEqual(account.email, "demo@random.example")
        self.assertEqual(mailbox._session.get.call_count, 1)
        _, kwargs = mailbox._session.get.call_args
        self.assertEqual(kwargs.get("params"), {})

    def test_get_email_domain_list_supports_object_items(self):
        mailbox = create_mailbox(
            "freemail",
            extra={
                "freemail_api_url": "https://freemail.example",
                "freemail_domain": "target.example",
            },
        )
        mailbox._session = mock.Mock()
        mailbox._session.get.side_effect = [
            _response({"domains": [{"domain": "fallback.example"}, {"domain": "target.example"}]}),
            _response({"email": "demo@target.example"}),
        ]

        account = mailbox.get_email()

        self.assertEqual(account.email, "demo@target.example")
        _, kwargs = mailbox._session.get.call_args
        self.assertEqual(kwargs.get("params"), {"domainIndex": 1})


def _response(payload):
    response = mock.Mock()
    response.json.return_value = payload
    return response


if __name__ == "__main__":
    unittest.main()

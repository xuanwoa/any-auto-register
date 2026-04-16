import ast
import unittest
from pathlib import Path


class HealthApiTests(unittest.TestCase):
    def test_health_route_exists(self):
        source = Path("main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        found = False
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "app"
                    and func.attr == "get"
                ):
                    continue
                if decorator.args and isinstance(decorator.args[0], ast.Constant) and decorator.args[0].value == "/api/health":
                    found = True
                    break
            if found:
                break

        self.assertTrue(found, "expected /api/health route to exist")


if __name__ == "__main__":
    unittest.main()

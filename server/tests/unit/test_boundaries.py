import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src/music_dashboard"


def test_domain_has_no_outward_imports() -> None:
    forbidden = ("fastapi", "sqlalchemy", "http", "httpx", "requests", "urllib", "psycopg")
    for path in (ROOT / "domain").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            for name in imports:
                assert not name.startswith(forbidden), (path, name)
                if name.startswith("music_dashboard."):
                    assert name.startswith("music_dashboard.domain."), (path, name)

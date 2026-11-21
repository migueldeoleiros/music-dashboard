import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src/music_dashboard"


def test_domain_has_no_outward_imports() -> None:
    forbidden = (
        "fastapi",
        "sqlalchemy",
        "http",
        "httpx",
        "httpcore",
        "requests",
        "urllib",
        "aiohttp",
        "psycopg",
    )
    for path in (ROOT / "domain").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    package = ".".join(path.relative_to(ROOT.parent).with_suffix("").parts[:-1])
                    name = resolve_name("." * node.level + name, package)
                imports = [name]
            for name in imports:
                assert not name.startswith(forbidden), (path, name)
                if name.startswith("music_dashboard."):
                    assert name.startswith("music_dashboard.domain."), (path, name)
                else:
                    assert name.split(".")[0] in sys.stdlib_module_names, (path, name)

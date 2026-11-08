"""Guard: COD must import openpilot modules through the `openpilot.` package.

At v0.11.2 upstream moved cereal/, common/, selfdrive/, system/ and tools/
under a real `openpilot/` package; the top-level directories are gone. A bare
`from cereal import log` raises ModuleNotFoundError on the device, and most
call sites swallow it (log_parser logs at debug level), so the failure shows
up only as silently empty route metadata.
"""

import ast
import re
from pathlib import Path

import pytest

COD_ROOT = Path(__file__).resolve().parent.parent
MOVED_PACKAGES = {"cereal", "common", "selfdrive", "system", "tools"}
SKIP_DIRS = {"node_modules", "tests", "frontend", "static", "dist", "build", "lib",
             "__pycache__", ".venv"}


def _cod_sources():
    for path in sorted(COD_ROOT.rglob("*.py")):
        if SKIP_DIRS.intersection(path.relative_to(COD_ROOT).parts):
            continue
        yield path


def _bare_imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            if name.split(".")[0] in MOVED_PACKAGES:
                yield node.lineno, name


@pytest.mark.parametrize("path", list(_cod_sources()),
                         ids=lambda p: str(p.relative_to(COD_ROOT)))
def test_no_bare_openpilot_imports(path):
    found = [f"{path.relative_to(COD_ROOT)}:{line} imports {name}"
             for line, name in _bare_imports(path)]
    assert not found, "use the openpilot. prefix:\n" + "\n".join(found)


# A relative path into a moved package, not preceded by a path segment
# ("openpilot/selfdrive/..." and absolute "/system/bin" don't match).
_OLD_LAYOUT_PATH = re.compile(r"(?<![\w/.])(?:cereal|common|selfdrive|system|tools)/\w")


def _old_layout_paths(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings and _OLD_LAYOUT_PATH.search(node.value)):
            yield node.lineno, node.value


@pytest.mark.parametrize("path", list(_cod_sources()),
                         ids=lambda p: str(p.relative_to(COD_ROOT)))
def test_no_old_layout_paths(path):
    found = [f"{path.relative_to(COD_ROOT)}:{line} {value!r}"
             for line, value in _old_layout_paths(path)]
    assert not found, "prefix with openpilot/:\n" + "\n".join(found)

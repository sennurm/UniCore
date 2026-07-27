"""Enforces the module-boundary rules from ARCHITECTURE.md.

CI fails on a violating import, so the vertical-slice boundaries cannot rot:
1. router -> service -> dao -> models, one-way (router never touches dao/models).
2. Cross-module imports go through the other module's service only.
3. core/ never imports from modules/.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "unicore"
MODULES_DIR = SRC / "modules"

LAYER_FORBIDDEN_SUFFIXES: dict[str, tuple[str, ...]] = {
    "router": (".dao", ".models"),
    "schemas": (".dao", ".models", ".service", ".router"),
    "dao": (".service", ".router"),
    "models": (".service", ".dao", ".router", ".schemas"),
}


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found: list[str] = []
    package_parts = path.relative_to(SRC.parent).with_suffix("").parts[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:  # resolve relative imports against the file's package
                anchor = package_parts[: len(package_parts) - (node.level - 1)]
                base = ".".join(anchor) + ("." + node.module if node.module else "")
            found.extend(f"{base}.{alias.name}" for alias in node.names)
            found.append(base)
    return [f for f in found if f.startswith("unicore.")]


def _module_files() -> list[tuple[str, str, Path]]:
    out = []
    for pkg in sorted(p for p in MODULES_DIR.iterdir() if p.is_dir()):
        for f in sorted(pkg.glob("**/*.py")):
            out.append((pkg.name, f.stem, f))
    return out


def test_core_never_imports_modules() -> None:
    for f in sorted((SRC / "core").glob("**/*.py")):
        for imp in _imports_of(f):
            assert not imp.startswith("unicore.modules"), (
                f"core file {f.name} imports {imp} — core/ must not depend on modules/"
            )


def test_cross_module_imports_are_service_only() -> None:
    for module, _stem, f in _module_files():
        own_prefix = f"unicore.modules.{module}"
        for imp in _imports_of(f):
            if not imp.startswith("unicore.modules.") or imp.startswith(own_prefix):
                continue
            other = imp.removeprefix("unicore.modules.").split(".")
            layer_path = "." + ".".join(other[1:]) if len(other) > 1 else ""
            assert layer_path.startswith(".service") or layer_path == "", (
                f"{f} imports {imp} — cross-module access is service-to-service only"
            )


def test_layering_within_module_is_one_way() -> None:
    for module, stem, f in _module_files():
        forbidden = LAYER_FORBIDDEN_SUFFIXES.get(stem)
        if not forbidden:
            continue
        own_prefix = f"unicore.modules.{module}"
        for imp in _imports_of(f):
            if not imp.startswith(own_prefix):
                continue
            layer_path = imp.removeprefix(own_prefix)
            assert not any(
                layer_path == suffix or layer_path.startswith(suffix + ".")
                for suffix in forbidden
            ), f"{f} imports {imp} — violates router→service→dao→models layering"


def test_every_active_module_has_all_layers() -> None:
    for pkg in sorted(p for p in MODULES_DIR.iterdir() if p.is_dir()):
        files = {f.name for f in pkg.glob("*.py")}
        if "router.py" in files:  # active module — must carry the full slice
            missing = {"schemas.py", "service.py", "dao.py", "models.py"} - files
            assert not missing, f"module {pkg.name} is missing layers: {missing}"

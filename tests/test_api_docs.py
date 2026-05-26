"""API documentation coverage tests."""

from __future__ import annotations

from pathlib import Path
from runpy import run_path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "openbench"
API_DOCS_ROOT = PROJECT_ROOT / "docs" / "api"


def _module_name(path: Path) -> str:
    """Return the importable module name for an OpenBench Python file.

    Args:
        path: Python file path below the project root.

    Returns:
        Dotted import path represented by ``path``.
    """

    relative = path.relative_to(PROJECT_ROOT)
    if relative.name == "__init__.py":
        return ".".join(relative.parent.parts)
    return ".".join(relative.with_suffix("").parts)


def _iter_openbench_modules() -> list[str]:
    """Return all importable OpenBench package and module names.

    Returns:
        Sorted dotted module names discovered from ``openbench/**/*.py``.
    """

    modules = [_module_name(path) for path in PACKAGE_ROOT.rglob("*.py")]
    return sorted(modules)


def test_api_docs_cover_every_openbench_module() -> None:
    """Verify every package module has a generated autodoc page."""

    missing = [
        module
        for module in _iter_openbench_modules()
        if not (API_DOCS_ROOT / f"{module}.rst").is_file()
    ]

    assert missing == []


def test_api_docs_use_sphinx_automodule_directives() -> None:
    """Verify each API page delegates public reference content to autodoc."""

    missing_directive = []
    missing_options = []

    for module in _iter_openbench_modules():
        rst_path = API_DOCS_ROOT / f"{module}.rst"
        text = rst_path.read_text(encoding="utf-8")

        if f".. automodule:: {module}" not in text:
            missing_directive.append(module)

        for option in (":members:", ":undoc-members:", ":show-inheritance:"):
            if option not in text:
                missing_options.append(f"{module}: {option}")

    assert missing_directive == []
    assert missing_options == []


def test_sphinx_conf_enables_autodoc_google_docstrings() -> None:
    """Verify documentation builds use autodoc and Google-style docstrings."""

    conf: dict[str, Any] = run_path(str(PROJECT_ROOT / "docs" / "conf.py"))
    extensions = conf["extensions"]

    assert "sphinx.ext.autodoc" in extensions
    assert "sphinx.ext.napoleon" in extensions
    assert conf["napoleon_google_docstring"] is True
    assert conf["autodoc_typehints"] == "description"

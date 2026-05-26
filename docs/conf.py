"""Sphinx configuration for the OpenBench documentation."""

from __future__ import annotations

from importlib import metadata
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

project = "OpenBench"
author = "ROMERUU-dev"
try:
    release = metadata.version("openbench")
except metadata.PackageNotFoundError:
    release = "0.1.0"
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

source_suffix = {
    ".rst": "restructuredtext",
}
master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
suppress_warnings = [
    # Package __init__ modules intentionally re-export symbols documented again
    # in their concrete implementation modules.
    "ref.python",
]

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "ignore-module-all": True,
}
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_format = "short"
autosummary_generate = True

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_use_ivar = True

html_theme = "alabaster"
html_static_path: list[str] = []

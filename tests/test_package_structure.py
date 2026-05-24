"""Package structure smoke tests."""

from __future__ import annotations

import importlib


def test_openbench_imports() -> None:
    """Verify the top-level package imports without optional hardware backends."""

    module = importlib.import_module("openbench")

    assert module.__version__


def test_claude_package_modules_import() -> None:
    """Verify the package modules documented in CLAUDE.md are importable."""

    modules = [
        "openbench.core.interfaces",
        "openbench.core.orchestrator",
        "openbench.core.experiment",
        "openbench.core.session",
        "openbench.backends.virtualbench_backend",
        "openbench.backends.sr860_backend",
        "openbench.backends.keysight_backend",
        "openbench.backends.rigol_backend",
        "openbench.backends.tektronix_backend",
        "openbench.experiments.dc_sweep",
        "openbench.experiments.frequency_sweep",
        "openbench.experiments.impedance_sweep",
        "openbench.experiments.chua_admittance",
        "openbench.experiments.component_char",
        "openbench.filters.design",
        "openbench.filters.topologies",
        "openbench.filters.validation",
        "openbench.gui.main",
        "openbench.gui.theme",
        "openbench.gui.widgets",
        "openbench.gui.panels",
        "openbench.utils.scpi_helpers",
        "openbench.utils.data_export",
        "openbench.data.recorder",
        "openbench.data.plotter",
    ]

    for module_name in modules:
        assert importlib.import_module(module_name)

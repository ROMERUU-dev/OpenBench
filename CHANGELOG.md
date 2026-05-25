# Changelog

All notable changes to OpenBench are documented in this file.

The format follows Keep a Changelog, and this project uses Semantic Versioning
while it remains in alpha.

## [Unreleased]

### Added

- Initial changelog created for phase `1-setup`.

## [0.1.0] - 2026-05-24

### Added

- Initial package skeleton for composition-first lab instrument orchestration.
- Core interfaces, orchestration, experiment, and session modules under
  `openbench.core`.
- Backend wrapper modules for VirtualBench, SR860, Keysight, Rigol, and
  Tektronix integrations under `openbench.backends`.
- Experiment modules for DC sweeps, frequency sweeps, impedance sweeps, Chua
  admittance, and component characterization under `openbench.experiments`.
- Integrated SOFIA filter-design namespace under `openbench.filters`.
- CustomTkinter GUI namespace and theme scaffolding under `openbench.gui`.
- Data and utility namespaces for recorders, plotters, SCPI helpers, and export.
- Simulation-oriented examples and pytest coverage for the lab workflow and
  backend smoke behavior.

### Notes

- OpenBench composes existing backend projects instead of reimplementing their
  hardware-specific behavior.
- Hardware-facing changes should preserve simulation mode so the Monday lab
  workflow can be validated without instruments.

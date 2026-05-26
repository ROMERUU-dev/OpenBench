# OpenBench — Architecture

> Version: 1.0 · Updated: 2026-05-25

## Table of Contents

1. [Overview](#1-overview)
2. [Design Principles](#2-design-principles)
3. [Layer Diagram](#3-layer-diagram)
4. [Module Map](#4-module-map)
5. [Core Layer](#5-core-layer)
   - 5.1 [Instrument Interfaces](#51-instrument-interfaces)
   - 5.2 [Orchestrator](#52-orchestrator)
   - 5.3 [Experiment Lifecycle](#53-experiment-lifecycle)
   - 5.4 [Measurement Session](#54-measurement-session)
6. [Backend Adapters](#6-backend-adapters)
7. [Experiments](#7-experiments)
8. [Filters — SOFIA Integration](#8-filters--sofia-integration)
9. [GUI](#9-gui)
10. [Data Layer](#10-data-layer)
11. [Typical Data Flows](#11-typical-data-flows)
12. [Simulation Mode](#12-simulation-mode)
13. [Directory Reference](#13-directory-reference)

---

## 1. Overview

OpenBench is a **composition layer** that ties together existing standalone instrument
driver projects under a single Python API and GUI. It does not reimplement instrument
communication; instead it wraps mature backend libraries and exposes uniform abstract
interfaces so that experiments remain portable across hardware.

```
┌──────────────────────────────────────────────────────────────┐
│                     User / GUI / Scripts                      │
└─────────────────────────┬────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────┐
│           openbench.experiments  /  openbench.filters         │
│       (reusable measurement workflows + SOFIA filter design)  │
└────────┬──────────┬──────────┬──────────────────────────────-─┘
         │          │          │
┌────────▼──┐ ┌─────▼───┐ ┌───▼──────────────────────────────┐
│  core/    │ │ data/   │ │  backends/                        │
│ interfaces│ │recorder │ │  (thin wrappers over existing     │
│orchestrat.│ │plotter  │ │   driver repos on the same host)  │
│experiment │ │         │ │                                    │
│session    │ └─────────┘ └────────────────────────────────────┘
└───────────┘
```

---

## 2. Design Principles

| Principle | Implication |
|---|---|
| **Composition, not reimplementation** | Backends import and wrap existing repos; zero duplication of SCPI logic |
| **Abstract interfaces as contract** | Experiments depend only on `IInstrument` sub-interfaces; never on concrete backends |
| **Simulation mandatory** | Every adapter and every experiment must run without hardware (`simulate=True`) |
| **Reproducibility** | `MeasurementSession` captures Git state, Python version, env vars, and artifact hashes |
| **SI units throughout** | All public APIs use volts, amperes, hertz, seconds; no driver-specific units leak out |

---

## 3. Layer Diagram

```
╔══════════════════════════════════════════════════════════════╗
║  GUI  (openbench.gui)                                        ║
║  CustomTkinter · dark/light theme · sidebar navigation       ║
╠══════════════════════════════════════════════════════════════╣
║  Experiments  (openbench.experiments)                        ║
║  DC Sweep · Frequency Sweep · Impedance Sweep                ║
║  Chua Admittance · Component Characterisation                ║
║  Filter Design Experiment                                     ║
╠══════════════════════════════════════════════════════════════╣
║  Filters / SOFIA  (openbench.filters)                        ║
║  FilterDesigner ──► sofia_filter_studio (external lib)       ║
╠══════════════════════════════════════════════════════════════╣
║  Core  (openbench.core)                                      ║
║  interfaces · orchestrator · experiment · session            ║
╠══════════════════════════════════════════════════════════════╣
║  Backends  (openbench.backends)                              ║
║  VirtualBench · SR860 · Keysight · Rigol · Tektronix         ║
╠══════════════════════════════════════════════════════════════╣
║  External Driver Repos  (standalone, unchanged)              ║
║  vbarrido-py · sr860-impedance-workbench                     ║
║  keysight_E36312A_DCSweep · rigol_ds1000e_python             ║
║  tektronix-tbs1000c-linux                                     ║
╚══════════════════════════════════════════════════════════════╝
```

---

## 4. Module Map

```
openbench/
├── core/
│   ├── interfaces.py       ← Abstract instrument contracts (IInstrument hierarchy)
│   ├── orchestrator.py     ← Registry, bulk connect/disconnect, VISA discovery
│   ├── experiment.py       ← BaseExperiment lifecycle + ExperimentResult
│   └── session.py          ← MeasurementSession, SessionManager, reproducibility
│
├── backends/
│   ├── virtualbench_backend.py   ← IOscilloscope + IFunctionGenerator via vbarrido-py
│   ├── sr860_backend.py          ← IImpedanceAnalyzer via sr860-impedance-workbench
│   ├── keysight_backend.py       ← IDCSupply via keysight_E36312A_DCSweep
│   ├── rigol_backend.py          ← IOscilloscope via rigol_ds1000e_python
│   └── tektronix_backend.py      ← IOscilloscope via tektronix-tbs1000c-linux
│
├── experiments/
│   ├── dc_sweep.py               ← IDCSupply → DCSweepReading[]
│   ├── frequency_sweep.py        ← IFunctionGenerator → FrequencySweepPoint[]
│   ├── impedance_sweep.py        ← IImpedanceAnalyzer → ImpedancePoint[]
│   ├── chua_admittance.py        ← SR860 + Keysight → admittance curve
│   ├── component_char.py         ← TC4069UBP + inductor characterisation
│   └── filter_design_experiment.py ← SOFIA design → optional sweep validation
│
├── filters/
│   ├── design.py           ← FilterDesigner wraps sofia_filter_studio
│   ├── topologies.py       ← DesignInputs, FilterSpec, Approximation, Topology enums
│   └── validation.py       ← Theory-vs-measurement comparison helpers
│
├── gui/
│   ├── app.py              ← OpenBenchApp (CTk root window)
│   ├── theme.py            ← ThemeManager, color palette, dark/light toggle
│   ├── panels/             ← ContentPanel subclasses (one per sidebar section)
│   └── widgets/            ← Reusable CTk widgets (StatusBar, ThemeToggle)
│
├── data/
│   ├── recorder.py         ← DataRecorder → CSV + JSON sidecar + HDF5 export
│   └── plotter.py          ← Matplotlib helpers for session results
│
└── utils/
    ├── scpi_helpers.py     ← SCPI string builders shared across backends
    └── data_export.py      ← HDF5ExportRecord, export_hdf5
```

---

## 5. Core Layer

### 5.1 Instrument Interfaces

`openbench/core/interfaces.py` defines the type hierarchy that all backends implement:

```
IInstrument  (dataclass, ABC)
│   name: str
│   resource: str | None
│   simulate: bool
│   connect() / disconnect()        ← lifecycle; idempotent
│   __enter__ / __exit__            ← context manager
│   _connect() / _disconnect()      ← abstract hooks for subclasses
│
├── IDCSupply
│       set_voltage(channel, voltage_v)
│       set_current(channel, current_a)
│       sweep(channel, start_v, stop_v, step_v, …) → list[DCSweepReading]
│
├── IFunctionGenerator
│       configure(WaveformConfig)
│       enable_output(channel, enabled)
│       sweep(channel, start_hz, stop_hz, num_points, …) → list[FrequencySweepPoint]
│
├── IOscilloscope
│       configure_channel(channel, volts_per_div, coupling, enabled)
│       configure_timebase(time_per_div_s, trigger_…)
│       acquire(channel) → OscilloscopeReading
│
└── IImpedanceAnalyzer
        measure_at_freq(frequency_hz, excitation_v, settle_periods) → ImpedancePoint
        sweep(start_hz, stop_hz, num_points, …) → list[ImpedancePoint]
```

**Key data classes** (all frozen, all-SI):

| Class | Purpose |
|---|---|
| `DCSweepReading` | Measured V/I at one DC setpoint |
| `WaveformConfig` | Full waveform specification for a function generator |
| `FrequencySweepPoint` | Single frequency step applied by a generator |
| `OscilloscopeReading` | Time-domain waveform with timestamps and voltage arrays |
| `ImpedancePoint` | Z_real, Z_imag, phase, magnitude at one frequency |

### 5.2 Orchestrator

`InstrumentOrchestrator` (`openbench/core/orchestrator.py`) is a registry and lifecycle
coordinator:

```
InstrumentOrchestrator
│   simulate: bool           ← propagates to all registered adapters when True
│   instruments: dict[str, IInstrument]
│
│   register(instrument)     ← adds adapter; raises on duplicate name
│   unregister(name)
│   get(name) → IInstrument | None
│   get_by_interface(T) → list[T]   ← experiments query by abstract role
│   list_instruments() → [(name, status)]
│
│   connect_all() → dict[str, Exception | None]   ← tolerant bulk connect
│   disconnect_all() → dict[str, Exception | None]
│   __enter__ / __exit__                          ← bulk lifecycle as context manager
│
└── discover_visa() → list[str]    ← optional pyvisa resource scan
```

Experiments access instruments through `get_by_interface()` so they never import
concrete backend classes.

### 5.3 Experiment Lifecycle

`BaseExperiment` (`openbench/core/experiment.py`) enforces a linear state machine:

```
IDLE ──► SETUP ──► RUNNING ──► TEARDOWN ──► COMPLETED
                                    │
                          (on error)├──► FAILED
                          (on abort)└──► ABORTED
```

Public surface:

```
BaseExperiment  (dataclass, ABC)
│   name: str
│   simulate: bool
│   on_progress: Callable[[str, float], None] | None
│
│   validate()          ← raise to abort before any hardware touch
│   setup()             ← connect instruments, apply initial settings
│   _run() → dict       ← REQUIRED: core measurement logic (abstract)
│   teardown()          ← always called, even after failure
│
│   run(simulate) → ExperimentResult
│   abort()             ← sets _abort_requested; honoured inside _run()
│   report_progress(msg, fraction)
│
└── __enter__ / __exit__ ← setup/teardown as context manager
```

`ExperimentResult` is a frozen dataclass carrying `name`, `data`, `state`,
`duration_s`, and `error`.

### 5.4 Measurement Session

`MeasurementSession` (`openbench/core/session.py`) provides reproducibility tracking:

```
MeasurementSession
│   name, session_id, tags, simulate
│   state: SessionState  (CREATED → RUNNING → COMPLETED | FAILED | ABORTED)
│
│   start() → Path          ← creates session dir, captures environment snapshot
│   close(state)            ← finalises manifest
│   fail(error)
│
│   add_metadata(key, value)
│   set_config(config)
│   record_event(name, metadata)
│   record_instrument(adapter)
│   record_result(ExperimentResult)     ← writes results/TIMESTAMP_name.json
│   write_json_artifact(name, payload)
│   register_artifact(path, kind)
│   save_manifest() → Path              ← manifest.json + manifest.yaml
│
└── from_manifest(path)     ← re-hydrate a closed session
```

Session directory layout:

```
openbench_sessions/
└── 20260525T142300Z_chua-lab_a1b2c3d4/
    ├── manifest.json       ← full session manifest (schema v1)
    ├── manifest.yaml       ← YAML mirror (optional, requires PyYAML)
    ├── config.json         ← reproducible config snapshot
    ├── config.yaml
    ├── results/
    │   └── 20260525T142301Z_chua-admittance.json
    └── artifacts/
        └── admittance_plot.png
```

Environment snapshot captured at `start()`: Python version, platform, Git commit,
branch, dirty flag, and a configurable set of environment variables.

`SessionManager` is a factory and lookup helper for multi-session workflows.

---

## 6. Backend Adapters

Each adapter in `openbench/backends/` wraps one existing standalone driver project
without reimplementing communication logic.

| Backend file | Implements | Wraps (external repo) | Hardware |
|---|---|---|---|
| `virtualbench_backend.py` | `IOscilloscope`, `IFunctionGenerator` | `vbarrido-py` | NI VirtualBench VB-8012 |
| `sr860_backend.py` | `IImpedanceAnalyzer` | `sr860-impedance-workbench` | Stanford Research SR860 lock-in |
| `keysight_backend.py` | `IDCSupply` | `keysight_E36312A_DCSweep` | Keysight E36312A DC supply |
| `rigol_backend.py` | `IOscilloscope` | `rigol_ds1000e_python` | Rigol DS1000E oscilloscope |
| `tektronix_backend.py` | `IOscilloscope` | `tektronix-tbs1000c-linux` | Tektronix TBS1000C oscilloscope |

**Adapter pattern:**

```
ConcreteBackend(IXxx)
│
│   __post_init__()   ← initialises backend-library handles (no I/O yet)
│   _connect()        ← imports & opens backend library session
│   _disconnect()     ← closes backend library session
│   <interface methods> ← delegates to backend library, converts units if needed
│
└── simulate path     ← returns synthetic data without touching hardware
```

SR860 multi-candidate discovery: the adapter probes a list of candidate VISA/serial
resources and uses the first one that responds, making it robust to port enumeration
differences across hosts.

---

## 7. Experiments

Built-in experiment classes live in `openbench/experiments/` and all extend
`BaseExperiment`.

| Class | Instruments used | Output |
|---|---|---|
| `DCSweep` | `IDCSupply` | `list[DCSweepReading]` |
| `FrequencySweep` | `IFunctionGenerator` | `list[FrequencySweepPoint]` |
| `ImpedanceSweep` | `IImpedanceAnalyzer` | `list[ImpedancePoint]` |
| `ChuaAdmittanceSweep` | `IImpedanceAnalyzer` + `IDCSupply` | Admittance vs. bias curve |
| `TC4069UBPCharacterization` | `IDCSupply` + `IOscilloscope` | Inverter transfer curve |
| `InductorCharacterization` | `IImpedanceAnalyzer` | L, R, Q vs. frequency |
| `FilterDesignExperiment` | SOFIA + optional `IImpedanceAnalyzer` | Design result + measured Bode |

The `chua_lab_workflow.py` example chains the last three experiment types in sequence
using a single `MeasurementSession` for the Monday lab session deadline.

---

## 8. Filters — SOFIA Integration

`openbench/filters/` integrates the `sofia_filter_studio` library as an OpenBench core
module. The integration adds only the glue between SOFIA's design output and
OpenBench's measurement workflows.

```
User code
    │
    ▼
FilterDesigner(DesignInputs)       openbench/filters/design.py
    │
    ├── .design() ──────────────► sofia_filter_studio.design.design_filter()
    │                              returns DesignResult (poles, stages, components)
    │
    ├── .render_netlist(result) ► sofia_filter_studio.netlist.render_netlist()
    │                              returns SPICE netlist string
    │
    ├── .format_result(result) ──► sofia_filter_studio.design.format_result()
    │                              returns indented JSON string
    │
    └── .measurement_setup(result) ── OpenBench-only method
                                      returns MeasurementSetup
                                      (start_hz, stop_hz, num_points, excitation_v)
                                      ── feeds directly into IImpedanceAnalyzer.sweep()
```

**Key types** (`openbench/filters/topologies.py`):

| Type | Description |
|---|---|
| `FilterKind` | `LOWPASS`, `HIGHPASS`, `BANDPASS`, `BANDSTOP` |
| `Approximation` | `BUTTERWORTH`, `CHEBYSHEV`, `BESSEL` |
| `Topology` | `SALLEN_KEY`, `MFB` |
| `FilterSpec` | `passband_hz`, `stopband_hz` (scalar or tuple for bandpass) |
| `DesignInputs` | Complete design specification passed to SOFIA |
| `DesignResult` | Poles, stage realizations, component values, warnings |

**Full integration flow:**

```
1. openbench.filters.design.FilterDesigner  ←  designs the filter via SOFIA
2. FilterDesigner.measurement_setup()       ←  derives sweep parameters
3. IImpedanceAnalyzer.sweep(…)             ←  measures real filter response
4. openbench.filters.validation             ←  compares theory vs. measurement
5. MeasurementSession.record_result()       ←  persists for reproducibility
```

---

## 9. GUI

The GUI (`openbench/gui/`) is built on **CustomTkinter** and follows a three-region
layout with swappable content panels.

```
OpenBenchApp (CTk root)
│
├── HeaderBar          ← title, subtitle, theme toggle, "Connect" button
│
├── SidebarPanel       ← collapsible navigation
│       SidebarSection: Instruments
│           items: Overview, Setup Wizard, VirtualBench, SR860, Keysight, Rigol, Tektronix
│       SidebarSection: Experiments
│           items: DC Sweep, Frequency Sweep, Impedance Sweep, Chua Admittance, Comp. Char.
│       SidebarSection: Filters (SOFIA)
│           items: Filter Design, Validation
│       SidebarSection: Data
│           items: Sessions, Plots
│
├── ContentArea        ← swaps ContentPanel instances based on sidebar selection
│       WelcomePanel
│       DashboardPanel
│       InstrumentSetupPanel
│       InstrumentsPanel
│       ExperimentsPanel
│       FiltersPanel
│       DataPanel
│       SessionHistoryPanel
│       LivePlotPanel
│
└── StatusBar          ← current section, connection state, progress messages
```

Navigation wiring: `SidebarPanel` emits a key string → `_KEY_TO_GROUP` maps it to
a panel group key → `ContentArea` selects and displays the corresponding
`ContentPanel` subclass.

`ThemeManager` (`openbench/gui/theme.py`) owns the color palette and notifies all
registered callbacks when the user toggles dark/light mode so every widget can
refresh its `fg_color` without a restart.

---

## 10. Data Layer

```
openbench/data/
├── recorder.py    ← DataRecorder
└── plotter.py     ← Matplotlib helpers

openbench/utils/
├── data_export.py ← HDF5ExportRecord, export_hdf5 (h5py)
└── scpi_helpers.py ← shared SCPI string builders
```

**DataRecorder** writes three artefact formats per dataset:

1. **CSV** — rows of measurement data, ordered field names
2. **JSON sidecar** — schema version, field names, row count, user metadata, SHA-256
3. **HDF5** (optional) — via `export_hdf5` when `h5py` is installed

All paths land inside `openbench_data/` by default and can be registered as
`SessionArtifact` entries in the active `MeasurementSession`.

---

## 11. Typical Data Flows

### Script / CLI workflow

```
Script
 │
 ├─ InstrumentOrchestrator(simulate=False)
 │       .register(KeysightBackend(…))
 │       .register(SR860Backend(…))
 │
 ├─ MeasurementSession("chua-lab").start()
 │
 ├─ ChuaAdmittanceSweep(orchestrator, config)
 │       .run()  →  ExperimentResult
 │
 ├─ session.record_result(result)
 │
 └─ session.close()
```

### GUI workflow

```
User clicks sidebar item
    │
    ▼
SidebarPanel._on_navigate(key)
    │
    ▼
ContentArea.navigate(group)  ←  selects ContentPanel subclass
    │
    ▼
Panel builds/refreshes its UI
    │
    └─ On "Run" button press:
           ExperimentPanel._run_experiment()
               BaseExperiment.run(simulate=…)
               → ExperimentResult
               → LivePlotPanel.update(result.data)
               → session.record_result(result)
```

### Filter design + validation workflow

```
FiltersPanel
 │
 ├─ FilterDesigner(DesignInputs).design()  →  DesignResult
 ├─ .render_netlist(result)                →  SPICE string (displayed / saved)
 ├─ .measurement_setup(result)             →  MeasurementSetup
 │
 └─ (optional) IImpedanceAnalyzer.sweep(
        start_hz=setup.start_hz,
        stop_hz=setup.stop_hz,
        num_points=setup.num_points,
        excitation_v=setup.excitation_v,
    )  →  list[ImpedancePoint]
           │
           └─ filters.validation.compare(theory, measured) → ComparisonResult
```

---

## 12. Simulation Mode

OpenBench has three simulation levels that can be combined:

| Level | How to activate | Effect |
|---|---|---|
| **Adapter-level** | `instrument.simulate = True` | `connect()` sets status to `SIMULATED`; all interface methods return synthetic data |
| **Orchestrator-level** | `InstrumentOrchestrator(simulate=True)` | Forces `simulate=True` on every adapter registered via `register()` |
| **Experiment-level** | `experiment.run(simulate=True)` | Overrides the instance flag for one run; passed as `_simulate` to `_run()` |

All experiments check `self._simulate` before any hardware call, so the full
`chua_lab_workflow.py` can be validated on a development laptop with no instruments
attached.

---

## 13. Directory Reference

```
OpenBench/
├── openbench/          ← installable Python package
│   ├── core/           ← abstract contracts + orchestration + session
│   ├── backends/       ← instrument adapters (one per driver repo)
│   ├── experiments/    ← reusable measurement workflows
│   ├── filters/        ← SOFIA filter design integration
│   ├── gui/            ← CustomTkinter application
│   ├── data/           ← recorder + plotter
│   └── utils/          ← SCPI helpers, HDF5 export
├── tests/              ← pytest test suite (one file per module)
├── examples/
│   └── chua_lab_workflow.py  ← Monday lab session workflow
├── docs/
│   ├── architecture.md       ← this file
│   ├── architecture/         ← supplementary architecture assets
│   ├── api/                  ← generated API reference (future)
│   └── manuals/              ← instrument-specific usage notes (future)
├── CLAUDE.md           ← AI assistant project context
├── CHANGELOG.md
├── README.md
└── pyproject.toml
```

### External driver repos (co-located on the same host)

| Repo directory | Backend that wraps it |
|---|---|
| `~/virtualBench-NI` | `virtualbench_backend.py` |
| `~/sr860-impedance-workbench` | `sr860_backend.py` |
| `~/keysight_E36312A_DCSweep` | `keysight_backend.py` |
| `~/rigol_ds1000e_python` | `rigol_backend.py` |
| `~/tektronix-tbs1000c-linux` | `tektronix_backend.py` |

These repos are not modified by OpenBench and continue to work as standalone tools.

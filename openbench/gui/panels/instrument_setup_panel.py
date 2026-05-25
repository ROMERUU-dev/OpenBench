"""Instrument setup panel with a connection wizard."""

from __future__ import annotations

import glob
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import customtkinter as ctk

from openbench.backends.keysight_backend import KeysightE36312ABackend
from openbench.backends.rigol_backend import RigolDS1000EBackend
from openbench.backends.sr860_backend import SR860Backend
from openbench.backends.tektronix_backend import TektronixTBS1000CBackend
from openbench.backends.virtualbench_backend import (
    VirtualBenchFGenBackend,
    VirtualBenchOscilloscopeBackend,
    VirtualBenchPSBackend,
)
from openbench.core.interfaces import IInstrument, InstrumentStatus
from openbench.core.orchestrator import InstrumentOrchestrator
from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)

ConnectionMode = Literal["simulate", "hardware"]
ResourceTransport = Literal["visa", "usbtmc", "virtualbench"]

_DISPLAY_MODE_TO_CONFIG: dict[str, ConnectionMode] = {
    "Simulation": "simulate",
    "Hardware": "hardware",
}
_CONFIG_MODE_TO_DISPLAY: dict[ConnectionMode, str] = {
    "simulate": "Simulation",
    "hardware": "Hardware",
}
_STATUS_COLOR_KEY: dict[InstrumentStatus, str] = {
    InstrumentStatus.CONNECTED: "success",
    InstrumentStatus.SIMULATED: "warning",
    InstrumentStatus.DISCONNECTED: "text_muted",
    InstrumentStatus.ERROR: "error",
}


@dataclass(frozen=True)
class InstrumentSetupSpec:
    """Describes one backend adapter available in the setup wizard.

    Attributes:
        key: Stable wizard identifier.
        display_name: User-facing instrument name.
        role: Short instrument role displayed in the setup list.
        backend_cls: Concrete OpenBench backend adapter class.
        default_name: Default name used in ``InstrumentOrchestrator``.
        transport: Resource discovery family for this adapter.
        resource_hint: Short placeholder shown near the resource entry.
    """

    key: str
    display_name: str
    role: str
    backend_cls: type[IInstrument]
    default_name: str
    transport: ResourceTransport
    resource_hint: str

    @property
    def backend_label(self) -> str:
        """Return the concrete backend class name for display and logging."""

        return self.backend_cls.__name__


@dataclass(frozen=True)
class InstrumentConnectionConfig:
    """Connection request collected by the wizard.

    Attributes:
        instrument_key: Key of the selected ``InstrumentSetupSpec``.
        mode: ``"simulate"`` to avoid hardware access, ``"hardware"`` to open
            a backend connection.
        resource: Optional resource string such as a VISA address, USBTMC path,
            or VirtualBench device name.
        name: Optional orchestrator registry name. Defaults to the setup spec's
            ``default_name`` when omitted.
    """

    instrument_key: str
    mode: ConnectionMode = "simulate"
    resource: str | None = None
    name: str | None = None

    @property
    def simulate(self) -> bool:
        """Whether this configuration should avoid physical hardware access."""

        return self.mode == "simulate"


@dataclass(frozen=True)
class InstrumentConnectionResult:
    """Outcome from a wizard connection operation.

    Attributes:
        instrument_key: Setup spec key that was attempted.
        instrument_name: Orchestrator registry name associated with the attempt.
        status: Adapter status after the attempt.
        ok: True when the operation completed without backend errors.
        message: Human-readable status summary for the GUI.
        error: Backend exception, when one occurred.
    """

    instrument_key: str
    instrument_name: str
    status: InstrumentStatus
    ok: bool
    message: str
    error: Exception | None = field(default=None, compare=False, repr=False)


_DEFAULT_SPECS: tuple[InstrumentSetupSpec, ...] = (
    InstrumentSetupSpec(
        key="virtualbench_scope",
        display_name="VirtualBench Scope",
        role="Oscilloscope",
        backend_cls=VirtualBenchOscilloscopeBackend,
        default_name="virtualbench-scope",
        transport="virtualbench",
        resource_hint="VB8012 device name, or blank for auto-discovery",
    ),
    InstrumentSetupSpec(
        key="virtualbench_fgen",
        display_name="VirtualBench Function Gen",
        role="Function Generator",
        backend_cls=VirtualBenchFGenBackend,
        default_name="virtualbench-fgen",
        transport="virtualbench",
        resource_hint="Same VirtualBench device name used by the scope",
    ),
    InstrumentSetupSpec(
        key="virtualbench_supply",
        display_name="VirtualBench DC Supply",
        role="DC Power Supply",
        backend_cls=VirtualBenchPSBackend,
        default_name="virtualbench-supply",
        transport="virtualbench",
        resource_hint="Same VirtualBench device name used by the scope",
    ),
    InstrumentSetupSpec(
        key="sr860",
        display_name="SR860 Lock-in",
        role="Impedance Analyzer",
        backend_cls=SR860Backend,
        default_name="sr860",
        transport="visa",
        resource_hint="VISA resource or /dev/usbtmc path",
    ),
    InstrumentSetupSpec(
        key="keysight",
        display_name="Keysight E36312A",
        role="DC Power Supply",
        backend_cls=KeysightE36312ABackend,
        default_name="keysight-e36312a",
        transport="visa",
        resource_hint="VISA resource string",
    ),
    InstrumentSetupSpec(
        key="rigol",
        display_name="Rigol DS1000E",
        role="Oscilloscope",
        backend_cls=RigolDS1000EBackend,
        default_name="rigol-ds1000e",
        transport="usbtmc",
        resource_hint="/dev/usbtmc0",
    ),
    InstrumentSetupSpec(
        key="tektronix",
        display_name="Tektronix TBS1000C",
        role="Oscilloscope",
        backend_cls=TektronixTBS1000CBackend,
        default_name="tektronix-tbs1000c",
        transport="usbtmc",
        resource_hint="/dev/usbtmc0",
    ),
)


def available_instrument_specs() -> tuple[InstrumentSetupSpec, ...]:
    """Return the backend adapters exposed by the setup wizard.

    Returns:
        Ordered tuple of setup specs. The order matches the GUI list.
    """

    return _DEFAULT_SPECS


class InstrumentConnectionWizard:
    """Headless connection-wizard model used by ``InstrumentSetupPanel``.

    The model keeps UI-independent connection state, builds concrete backend
    adapters from ``InstrumentConnectionConfig`` values, and registers
    successfully connected adapters with an ``InstrumentOrchestrator``.

    Args:
        orchestrator: Optional orchestrator to receive connected instruments.
        specs: Optional setup specs. Tests can pass a reduced list while the GUI
            uses the default OpenBench backends.
    """

    def __init__(
        self,
        orchestrator: InstrumentOrchestrator | None = None,
        specs: Sequence[InstrumentSetupSpec] | None = None,
    ) -> None:
        self._orchestrator = orchestrator or InstrumentOrchestrator()
        self._specs = tuple(specs) if specs is not None else available_instrument_specs()
        if not self._specs:
            raise ValueError("InstrumentConnectionWizard requires at least one setup spec.")
        self._spec_by_key = {spec.key: spec for spec in self._specs}
        if len(self._spec_by_key) != len(self._specs):
            raise ValueError("Instrument setup spec keys must be unique.")

        self._selected_key = self._specs[0].key
        self._mode: ConnectionMode = "simulate"
        self._resource_by_key: dict[str, str | None] = {}
        self._name_by_key: dict[str, str | None] = {}
        self._registered_name_by_key: dict[str, str] = {}
        self._last_result_by_key: dict[str, InstrumentConnectionResult] = {}

    @property
    def orchestrator(self) -> InstrumentOrchestrator:
        """Return the orchestrator receiving connected adapters."""

        return self._orchestrator

    @property
    def selected_key(self) -> str:
        """Return the currently selected setup spec key."""

        return self._selected_key

    @property
    def mode(self) -> ConnectionMode:
        """Return the current connection mode."""

        return self._mode

    def list_specs(self) -> tuple[InstrumentSetupSpec, ...]:
        """Return setup specs available to this wizard.

        Returns:
            Ordered tuple of setup specs.
        """

        return self._specs

    def get_spec(self, key: str) -> InstrumentSetupSpec:
        """Return a setup spec by key.

        Args:
            key: Setup spec identifier.

        Returns:
            Matching setup spec.

        Raises:
            KeyError: If ``key`` is not registered in this wizard.
        """

        return self._spec_by_key[key]

    def select_instrument(self, key: str) -> None:
        """Select the instrument edited by subsequent wizard operations.

        Args:
            key: Setup spec identifier.

        Raises:
            KeyError: If ``key`` is unknown.
        """

        self.get_spec(key)
        self._selected_key = key
        logger.debug("Instrument wizard selected key=%s", key)

    def set_mode(self, mode: ConnectionMode) -> None:
        """Set the wizard connection mode.

        Args:
            mode: ``"simulate"`` or ``"hardware"``.

        Raises:
            ValueError: If ``mode`` is unsupported.
        """

        if mode not in _CONFIG_MODE_TO_DISPLAY:
            raise ValueError(f"Unsupported connection mode: {mode!r}")
        self._mode = mode
        logger.debug("Instrument wizard mode=%s", mode)

    def set_resource(self, resource: str | None, *, key: str | None = None) -> None:
        """Set the resource string for an instrument.

        Args:
            resource: Resource identifier. Blank strings are stored as ``None``.
            key: Optional setup spec key. Defaults to the current selection.
        """

        target_key = key or self._selected_key
        self.get_spec(target_key)
        normalized = resource.strip() if resource else ""
        self._resource_by_key[target_key] = normalized or None
        logger.debug("Instrument wizard resource key=%s resource=%r", target_key, normalized)

    def set_name(self, name: str | None, *, key: str | None = None) -> None:
        """Set the orchestrator registry name for an instrument.

        Args:
            name: Registry name. Blank strings fall back to the setup default.
            key: Optional setup spec key. Defaults to the current selection.
        """

        target_key = key or self._selected_key
        self.get_spec(target_key)
        normalized = name.strip() if name else ""
        self._name_by_key[target_key] = normalized or None
        logger.debug("Instrument wizard name key=%s name=%r", target_key, normalized)

    def current_config(self) -> InstrumentConnectionConfig:
        """Return the connection config for the current wizard selection.

        Returns:
            Normalized current connection request.
        """

        return InstrumentConnectionConfig(
            instrument_key=self._selected_key,
            mode=self._mode,
            resource=self._resource_by_key.get(self._selected_key),
            name=self._name_by_key.get(self._selected_key),
        )

    def discover_resources(self, key: str | None = None) -> list[str]:
        """Discover likely resources for the selected instrument.

        Args:
            key: Optional setup spec key. Defaults to the current selection.

        Returns:
            Sorted list of resource strings. Discovery never raises; failures
            are logged and represented as an empty list.
        """

        target_key = key or self._selected_key
        spec = self.get_spec(target_key)
        if self._mode == "simulate":
            logger.debug("Resource discovery skipped in simulation mode for %s", spec.key)
            return []

        resources: list[str] = []
        if spec.transport == "visa":
            resources.extend(InstrumentOrchestrator.discover_visa(simulate=False))
            resources.extend(glob.glob("/dev/usbtmc*"))
        elif spec.transport == "usbtmc":
            resources.extend(glob.glob("/dev/usbtmc*"))
        elif spec.transport == "virtualbench":
            logger.info("VirtualBench discovery is delegated to the backend auto-discovery path.")

        unique = sorted(dict.fromkeys(resources))
        logger.info("Resource discovery key=%s found %d resource(s)", spec.key, len(unique))
        return unique

    def build_instrument(
        self,
        config: InstrumentConnectionConfig | None = None,
    ) -> IInstrument:
        """Build a concrete backend adapter from a connection config.

        Args:
            config: Optional connection config. Defaults to
                :meth:`current_config`.

        Returns:
            Backend adapter instance.

        Raises:
            KeyError: If the config references an unknown setup spec.
            ValueError: If the config mode is unsupported.
        """

        cfg = config or self.current_config()
        if cfg.mode not in _CONFIG_MODE_TO_DISPLAY:
            raise ValueError(f"Unsupported connection mode: {cfg.mode!r}")
        spec = self.get_spec(cfg.instrument_key)
        name = cfg.name.strip() if cfg.name else spec.default_name
        resource = cfg.resource.strip() if cfg.resource else None
        instrument = spec.backend_cls(name=name, resource=resource, simulate=cfg.simulate)
        logger.debug(
            "Built instrument key=%s name=%s backend=%s simulate=%s resource=%r",
            spec.key,
            name,
            spec.backend_label,
            cfg.simulate,
            resource,
        )
        return instrument

    def test_connection(
        self,
        config: InstrumentConnectionConfig | None = None,
    ) -> InstrumentConnectionResult:
        """Attempt a temporary connection without registering the adapter.

        Args:
            config: Optional connection config. Defaults to the current wizard
                state.

        Returns:
            Connection attempt result.
        """

        cfg = config or self.current_config()
        instrument = self.build_instrument(cfg)
        result = self._connect_instrument(instrument, cfg.instrument_key, register=False)
        if result.ok:
            try:
                instrument.disconnect()
            except Exception as exc:
                logger.warning("Temporary disconnect failed for %s: %s", instrument.name, exc)
        return result

    def connect_selected(self) -> InstrumentConnectionResult:
        """Connect and register the currently selected instrument.

        Returns:
            Connection result from the backend adapter.
        """

        cfg = self.current_config()
        instrument = self.build_instrument(cfg)
        return self._connect_instrument(instrument, cfg.instrument_key, register=True)

    def disconnect_selected(self) -> InstrumentConnectionResult:
        """Disconnect and unregister the currently selected instrument.

        Returns:
            Disconnection result.
        """

        key = self._selected_key
        spec = self.get_spec(key)
        name = self._registered_name_by_key.get(key) or self._name_by_key.get(key)
        name = name or spec.default_name
        return self._disconnect_registered(key, name)

    def connect_all_simulated(self) -> list[InstrumentConnectionResult]:
        """Connect every known adapter in simulation mode.

        Returns:
            Ordered list of connection results.
        """

        results: list[InstrumentConnectionResult] = []
        for spec in self._specs:
            cfg = InstrumentConnectionConfig(
                instrument_key=spec.key,
                mode="simulate",
                name=self._name_by_key.get(spec.key) or spec.default_name,
            )
            instrument = self.build_instrument(cfg)
            results.append(self._connect_instrument(instrument, spec.key, register=True))
        logger.info("Connected %d simulated instrument adapter(s)", len(results))
        return results

    def disconnect_all(self) -> list[InstrumentConnectionResult]:
        """Disconnect and unregister all adapters managed by this wizard.

        Returns:
            Ordered list of disconnection results.
        """

        keys_and_names = list(self._registered_name_by_key.items())
        results = [
            self._disconnect_registered(key, name)
            for key, name in keys_and_names
        ]
        logger.info("Disconnected %d instrument adapter(s)", len(results))
        return results

    def last_result(self, key: str) -> InstrumentConnectionResult | None:
        """Return the latest operation result for an instrument key.

        Args:
            key: Setup spec identifier.

        Returns:
            Last result, or ``None`` when no operation has run.
        """

        self.get_spec(key)
        return self._last_result_by_key.get(key)

    def _connect_instrument(
        self,
        instrument: IInstrument,
        key: str,
        *,
        register: bool,
    ) -> InstrumentConnectionResult:
        old_name = self._registered_name_by_key.get(key)
        if register and old_name and old_name != instrument.name:
            self._disconnect_registered(key, old_name)
        if register and self._orchestrator.get(instrument.name) is not None:
            self._disconnect_registered(key, instrument.name)

        try:
            instrument.connect()
        except Exception as exc:
            result = InstrumentConnectionResult(
                instrument_key=key,
                instrument_name=instrument.name,
                status=instrument.status(),
                ok=False,
                message=f"{instrument.name}: connection failed ({exc})",
                error=exc,
            )
            self._last_result_by_key[key] = result
            logger.warning(
                "Instrument connection failed key=%s name=%s: %s",
                key,
                instrument.name,
                exc,
            )
            return result

        if register:
            self._orchestrator.register(instrument)
            self._registered_name_by_key[key] = instrument.name

        result = InstrumentConnectionResult(
            instrument_key=key,
            instrument_name=instrument.name,
            status=instrument.status(),
            ok=True,
            message=f"{instrument.name}: {instrument.status().value}",
        )
        self._last_result_by_key[key] = result
        logger.info(
            "Instrument connection ok key=%s name=%s status=%s register=%s",
            key,
            instrument.name,
            instrument.status().value,
            register,
        )
        return result

    def _disconnect_registered(self, key: str, name: str) -> InstrumentConnectionResult:
        instrument = self._orchestrator.unregister(name)
        self._registered_name_by_key.pop(key, None)
        if instrument is None:
            result = InstrumentConnectionResult(
                instrument_key=key,
                instrument_name=name,
                status=InstrumentStatus.DISCONNECTED,
                ok=True,
                message=f"{name}: disconnected",
            )
            self._last_result_by_key[key] = result
            return result

        try:
            instrument.disconnect()
        except Exception as exc:
            result = InstrumentConnectionResult(
                instrument_key=key,
                instrument_name=name,
                status=instrument.status(),
                ok=False,
                message=f"{name}: disconnect failed ({exc})",
                error=exc,
            )
            self._last_result_by_key[key] = result
            logger.warning("Instrument disconnect failed key=%s name=%s: %s", key, name, exc)
            return result

        result = InstrumentConnectionResult(
            instrument_key=key,
            instrument_name=name,
            status=InstrumentStatus.DISCONNECTED,
            ok=True,
            message=f"{name}: disconnected",
        )
        self._last_result_by_key[key] = result
        logger.info("Instrument disconnected key=%s name=%s", key, name)
        return result


class InstrumentSetupPanel(ContentPanel):
    """GUI panel for configuring and connecting OpenBench instruments.

    The panel wraps ``InstrumentConnectionWizard`` with a CustomTkinter setup
    view. Connection operations are routed through existing backend adapters and
    ``InstrumentOrchestrator`` so standalone backend projects remain untouched.

    Args:
        master: Parent CustomTkinter widget.
        wizard: Optional wizard model for dependency injection.
        on_connection_change: Optional callback invoked with each connection
            result.
        **kwargs: Forwarded to ``ContentPanel``.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        wizard: InstrumentConnectionWizard | None = None,
        on_connection_change: Callable[[InstrumentConnectionResult], None] | None = None,
        **kwargs,
    ) -> None:
        self._wizard = wizard or InstrumentConnectionWizard()
        self._on_connection_change = on_connection_change
        self._spec_buttons: dict[str, ctk.CTkButton] = {}
        self._resource_buttons: list[ctk.CTkButton] = []
        super().__init__(master, **kwargs)

    @property
    def wizard(self) -> InstrumentConnectionWizard:
        """Return the non-visual wizard model owned by this panel."""

        return self._wizard

    def set_selected_instrument(self, key: str) -> None:
        """Select an instrument in the wizard and refresh panel controls.

        Args:
            key: Setup spec identifier.
        """

        if hasattr(self, "_name_var"):
            self._collect_controls()
        self._wizard.select_instrument(key)
        self._sync_controls_from_model()
        self._refresh_selection_buttons()

    def discover_resources(self) -> list[str]:
        """Run resource discovery for the current selection.

        Returns:
            List of discovered resource strings.
        """

        self._collect_controls()
        resources = self._wizard.discover_resources()
        self._render_discovered_resources(resources)
        if resources:
            self._set_status(f"Found {len(resources)} resource(s)", ok=True)
        else:
            self._set_status("No resources found", ok=False)
        return resources

    def test_connection(self) -> InstrumentConnectionResult:
        """Run a temporary connection check for the current selection.

        Returns:
            Connection test result.
        """

        self._collect_controls()
        result = self._wizard.test_connection()
        self._after_connection_result(result)
        return result

    def connect_selected(self) -> InstrumentConnectionResult:
        """Connect and register the current instrument.

        Returns:
            Connection result.
        """

        self._collect_controls()
        result = self._wizard.connect_selected()
        self._after_connection_result(result)
        return result

    def disconnect_selected(self) -> InstrumentConnectionResult:
        """Disconnect the current instrument.

        Returns:
            Disconnection result.
        """

        result = self._wizard.disconnect_selected()
        self._after_connection_result(result)
        return result

    def disconnect_all(self) -> list[InstrumentConnectionResult]:
        """Disconnect all instruments managed by the wizard.

        Returns:
            Ordered list of disconnection results.
        """

        results = self._wizard.disconnect_all()
        for result in results:
            self._after_connection_result(result, notify=False)
        self._set_status(f"Disconnected {len(results)} adapter(s)", ok=True)
        return results

    def connect_all_simulated(self) -> list[InstrumentConnectionResult]:
        """Connect all wizard instruments in simulation mode.

        Returns:
            Ordered list of connection results.
        """

        results = self._wizard.connect_all_simulated()
        for result in results:
            self._after_connection_result(result, notify=False)
        self._set_status(f"Simulation ready: {len(results)} adapter(s)", ok=True)
        return results

    def _build(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))

        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)

        header = ctk.CTkFrame(self, fg_color="transparent", height=74)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 0))
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Instrument Setup",
            font=(ff, 22, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Connection wizard for OpenBench backend adapters",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w")

        ctk.CTkButton(
            header,
            text="Simulate All",
            width=112,
            height=32,
            corner_radius=8,
            fg_color=colors["accent_primary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            command=self.connect_all_simulated,
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(8, 8))

        ctk.CTkButton(
            header,
            text="Disconnect All",
            width=118,
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self.disconnect_all,
        ).grid(row=0, column=2, rowspan=2, sticky="e")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=16)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)

        self._instrument_list = ctk.CTkScrollableFrame(
            body,
            corner_radius=8,
            fg_color=colors["bg_secondary"],
            label_text="",
        )
        self._instrument_list.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self._instrument_list.columnconfigure(0, weight=1)
        self._build_instrument_list()

        self._wizard_frame = ctk.CTkFrame(
            body,
            corner_radius=8,
            fg_color=colors["bg_secondary"],
        )
        self._wizard_frame.grid(row=0, column=1, sticky="nsew")
        self._wizard_frame.columnconfigure(0, weight=1)
        self._build_wizard_form()

        footer = ctk.CTkFrame(self, fg_color="transparent", height=34)
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 10))
        footer.grid_propagate(False)
        footer.columnconfigure(0, weight=1)
        self._status_label = ctk.CTkLabel(
            footer,
            text="Simulation mode is selected by default",
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._status_label.grid(row=0, column=0, sticky="w")

        self._sync_controls_from_model()
        self._refresh_selection_buttons()

    def _build_instrument_list(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))
        for row, spec in enumerate(self._wizard.list_specs()):
            card = ctk.CTkButton(
                self._instrument_list,
                text=f"{spec.display_name}\n{spec.role}",
                height=58,
                corner_radius=8,
                anchor="w",
                font=(ff, 12),
                fg_color="transparent",
                hover_color=colors["sidebar_active"],
                text_color=colors["text_secondary"],
                command=lambda k=spec.key: self.set_selected_instrument(k),
            )
            card.grid(row=row, column=0, sticky="ew", padx=8, pady=(8 if row == 0 else 4, 4))
            self._spec_buttons[spec.key] = card

    def _build_wizard_form(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))
        pad_x = 18

        self._selected_title = ctk.CTkLabel(
            self._wizard_frame,
            text="",
            font=(ff, 18, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        )
        self._selected_title.grid(row=0, column=0, sticky="ew", padx=pad_x, pady=(18, 2))

        self._selected_meta = ctk.CTkLabel(
            self._wizard_frame,
            text="",
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._selected_meta.grid(row=1, column=0, sticky="ew", padx=pad_x, pady=(0, 14))

        self._mode_var = ctk.StringVar(value=_CONFIG_MODE_TO_DISPLAY[self._wizard.mode])
        self._mode_control = ctk.CTkSegmentedButton(
            self._wizard_frame,
            values=list(_DISPLAY_MODE_TO_CONFIG),
            variable=self._mode_var,
            command=self._on_mode_change,
            selected_color=colors["accent_primary"],
            selected_hover_color=colors["accent_hover"],
            unselected_color=colors["bg_input"],
            unselected_hover_color=colors["sidebar_active"],
            text_color=colors["text_primary"],
        )
        self._mode_control.grid(row=2, column=0, sticky="ew", padx=pad_x, pady=(0, 12))

        self._name_var = ctk.StringVar(value="")
        self._name_entry = ctk.CTkEntry(
            self._wizard_frame,
            textvariable=self._name_var,
            height=34,
            corner_radius=8,
            fg_color=colors["bg_input"],
            border_color=colors["border"],
            text_color=colors["text_primary"],
            placeholder_text="Orchestrator name",
        )
        self._name_entry.grid(row=3, column=0, sticky="ew", padx=pad_x, pady=(0, 10))

        self._resource_var = ctk.StringVar(value="")
        self._resource_entry = ctk.CTkEntry(
            self._wizard_frame,
            textvariable=self._resource_var,
            height=34,
            corner_radius=8,
            fg_color=colors["bg_input"],
            border_color=colors["border"],
            text_color=colors["text_primary"],
            placeholder_text="Resource",
        )
        self._resource_entry.grid(row=4, column=0, sticky="ew", padx=pad_x, pady=(0, 4))

        self._resource_hint_label = ctk.CTkLabel(
            self._wizard_frame,
            text="",
            font=(ff, 10),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._resource_hint_label.grid(row=5, column=0, sticky="ew", padx=pad_x, pady=(0, 12))

        action_row = ctk.CTkFrame(self._wizard_frame, fg_color="transparent")
        action_row.grid(row=6, column=0, sticky="ew", padx=pad_x, pady=(0, 12))
        for col in range(4):
            action_row.columnconfigure(col, weight=1)

        self._detect_button = ctk.CTkButton(
            action_row,
            text="Detect",
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self.discover_resources,
        )
        self._detect_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            action_row,
            text="Test",
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self.test_connection,
        ).grid(row=0, column=1, sticky="ew", padx=6)

        ctk.CTkButton(
            action_row,
            text="Connect",
            height=32,
            corner_radius=8,
            fg_color=colors["accent_primary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            command=self.connect_selected,
        ).grid(row=0, column=2, sticky="ew", padx=6)

        ctk.CTkButton(
            action_row,
            text="Disconnect",
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self.disconnect_selected,
        ).grid(row=0, column=3, sticky="ew", padx=(6, 0))

        self._resources_frame = ctk.CTkFrame(
            self._wizard_frame,
            corner_radius=8,
            fg_color=colors["bg_card"],
        )
        self._resources_frame.grid(row=7, column=0, sticky="ew", padx=pad_x, pady=(0, 12))
        self._resources_frame.columnconfigure(0, weight=1)

        self._resource_results_label = ctk.CTkLabel(
            self._resources_frame,
            text="No discovery results",
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._resource_results_label.grid(row=0, column=0, sticky="ew", padx=12, pady=10)

        self._status_badge = ctk.CTkLabel(
            self._wizard_frame,
            text="Disconnected",
            font=(ff, 12, "bold"),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._status_badge.grid(row=8, column=0, sticky="ew", padx=pad_x, pady=(0, 18))

    def _sync_controls_from_model(self) -> None:
        spec = self._wizard.get_spec(self._wizard.selected_key)
        cfg = self._wizard.current_config()
        self._selected_title.configure(text=spec.display_name)
        self._selected_meta.configure(text=f"{spec.role} | {spec.backend_label}")
        self._resource_hint_label.configure(text=spec.resource_hint)
        self._mode_var.set(_CONFIG_MODE_TO_DISPLAY[cfg.mode])
        self._name_var.set(cfg.name or spec.default_name)
        self._resource_var.set(cfg.resource or "")
        result = self._wizard.last_result(spec.key)
        status = result.status if result else InstrumentStatus.DISCONNECTED
        self._update_status_badge(status)

    def _refresh_selection_buttons(self) -> None:
        colors = theme_manager.get_colors()
        for key, button in self._spec_buttons.items():
            active = key == self._wizard.selected_key
            button.configure(
                fg_color=colors["sidebar_active"] if active else "transparent",
                text_color=colors["text_primary"] if active else colors["text_secondary"],
            )

    def _collect_controls(self) -> None:
        mode = _DISPLAY_MODE_TO_CONFIG.get(self._mode_var.get(), "simulate")
        self._wizard.set_mode(mode)
        self._wizard.set_name(self._name_var.get())
        self._wizard.set_resource(self._resource_var.get())

    def _on_mode_change(self, value: str) -> None:
        mode = _DISPLAY_MODE_TO_CONFIG.get(value, "simulate")
        self._wizard.set_mode(mode)
        if mode == "simulate":
            self._set_status("Simulation mode selected", ok=True)
        else:
            self._set_status("Hardware mode selected", ok=True)

    def _render_discovered_resources(self, resources: list[str]) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))
        for button in self._resource_buttons:
            button.destroy()
        self._resource_buttons.clear()

        if not resources:
            self._resource_results_label.configure(text="No discovery results")
            return

        self._resource_results_label.configure(text="Select a discovered resource")
        for idx, resource in enumerate(resources, start=1):
            button = ctk.CTkButton(
                self._resources_frame,
                text=resource,
                height=28,
                corner_radius=6,
                anchor="w",
                fg_color="transparent",
                hover_color=colors["sidebar_active"],
                text_color=colors["text_primary"],
                font=(ff, 10),
                command=lambda r=resource: self._set_resource_from_discovery(r),
            )
            button.grid(row=idx, column=0, sticky="ew", padx=8, pady=(0, 6))
            self._resource_buttons.append(button)

    def _set_resource_from_discovery(self, resource: str) -> None:
        self._resource_var.set(resource)
        self._wizard.set_resource(resource)
        self._set_status(f"Resource selected: {resource}", ok=True)

    def _after_connection_result(
        self,
        result: InstrumentConnectionResult,
        *,
        notify: bool = True,
    ) -> None:
        self._update_status_badge(result.status)
        self._set_status(result.message, ok=result.ok)
        if notify and self._on_connection_change is not None:
            self._on_connection_change(result)

    def _update_status_badge(self, status: InstrumentStatus) -> None:
        colors = theme_manager.get_colors()
        color_key = _STATUS_COLOR_KEY.get(status, "text_muted")
        self._status_badge.configure(
            text=status.value.title(),
            text_color=colors.get(color_key, colors["text_muted"]),
        )

    def _set_status(self, message: str, *, ok: bool) -> None:
        colors = theme_manager.get_colors()
        self._status_label.configure(
            text=message,
            text_color=colors["success"] if ok else colors["warning"],
        )
        logger.info("InstrumentSetupPanel status: %s", message)

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_instrument_list"):
            self._instrument_list.configure(fg_color=colors["bg_secondary"])
        if hasattr(self, "_wizard_frame"):
            self._wizard_frame.configure(fg_color=colors["bg_secondary"])
        if hasattr(self, "_resources_frame"):
            self._resources_frame.configure(fg_color=colors["bg_card"])


__all__ = [
    "ConnectionMode",
    "InstrumentConnectionConfig",
    "InstrumentConnectionResult",
    "InstrumentConnectionWizard",
    "InstrumentSetupPanel",
    "InstrumentSetupSpec",
    "available_instrument_specs",
]

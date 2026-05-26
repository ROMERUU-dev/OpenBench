"""Measurement session lifecycle management and reproducibility metadata."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum, StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

from openbench.core.experiment import ExperimentResult

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = 1
DEFAULT_SESSION_ROOT = Path("openbench_sessions")
DEFAULT_ENV_KEYS = (
    "OPENBENCH_MODE",
    "OPENBENCH_SESSION",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CONDA_DEFAULT_ENV",
)


class SessionState(StrEnum):
    """Lifecycle state of a measurement session."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(frozen=True)
class SessionArtifact:
    """File artifact registered in a measurement session manifest.

    Attributes:
        name: Human-readable artifact label.
        kind: Artifact category such as ``experiment_result`` or ``config``.
        path: Path relative to the session directory when possible.
        sha256: SHA-256 digest of the file bytes.
        size_bytes: Artifact size in bytes.
        created_at: UTC timestamp when the artifact was registered.
        metadata: Optional structured details that help reproduce the artifact.
    """

    name: str
    kind: str
    path: str
    sha256: str
    size_bytes: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable artifact representation.

        Returns:
            Manifest-ready dictionary for this artifact.
        """

        return _to_jsonable(asdict(self))


@dataclass(frozen=True)
class SessionEvent:
    """Timestamped lifecycle event recorded in a measurement session.

    Attributes:
        name: Event name, for example ``session_started``.
        timestamp: UTC timestamp for the event.
        metadata: Optional structured event metadata.
    """

    name: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable event representation.

        Returns:
            Manifest-ready dictionary for this event.
        """

        return _to_jsonable(asdict(self))


@dataclass
class MeasurementSession:
    """Manage one reproducible OpenBench measurement session.

    The class remains compatible with the earlier lightweight
    ``MeasurementSession(name=...)`` usage while adding a real session
    lifecycle. Calling ``start()`` creates a session directory, captures
    environment metadata, and writes a manifest. Results and artifacts can then
    be attached without coupling experiments or backends to the session system.

    Attributes:
        name: Human-readable session name.
        created_at: UTC timestamp when the object was created.
        metadata: User-provided structured metadata.
        root_dir: Base directory where session directories are created.
        session_id: Stable session identifier and directory name. Generated on
            ``start()`` when omitted.
        simulate: Whether this session is a simulation run.
        tags: Optional labels for search and filtering.
    """

    name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)
    root_dir: Path | str = DEFAULT_SESSION_ROOT
    session_id: str | None = None
    simulate: bool = False
    tags: list[str] = field(default_factory=list)

    state: SessionState = field(default=SessionState.CREATED, init=False)
    started_at: datetime | None = field(default=None, init=False)
    ended_at: datetime | None = field(default=None, init=False)
    config: Mapping[str, Any] | None = field(default=None, init=False)
    environment: Mapping[str, Any] = field(default_factory=dict, init=False)
    instruments: list[dict[str, Any]] = field(default_factory=list, init=False)
    events: list[SessionEvent] = field(default_factory=list, init=False)
    artifacts: list[SessionArtifact] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        """Normalize constructor inputs without starting the session."""

        self.root_dir = Path(self.root_dir)
        self.created_at = _ensure_utc(self.created_at)
        self.metadata = dict(self.metadata)
        self.tags = list(self.tags)

    @property
    def path(self) -> Path | None:
        """Return the session directory path when a session id exists."""

        if self.session_id is None:
            return None
        return self.root_dir / self.session_id

    @property
    def duration_s(self) -> float | None:
        """Return elapsed session duration in seconds when available."""

        start = self.started_at
        end = self.ended_at
        if start is None:
            return None
        if end is None:
            return (datetime.now(UTC) - start).total_seconds()
        return (end - start).total_seconds()

    def add_metadata(self, key: str, value: Any) -> None:
        """Attach metadata to the session.

        Args:
            key: Metadata key.
            value: JSON-serializable metadata value.

        Raises:
            ValueError: If ``key`` is empty.
        """

        if not key:
            raise ValueError("Session metadata key must not be empty.")

        logger.debug("Adding session metadata: %s", key)
        self.metadata[key] = value
        if self.path is not None and self.path.exists():
            self.save_manifest()

    def update_metadata(self, values: Mapping[str, Any]) -> None:
        """Merge structured metadata into the session metadata.

        Args:
            values: Metadata mapping to merge into the session.
        """

        for key, value in values.items():
            self.add_metadata(str(key), value)

    def set_config(self, config: Mapping[str, Any] | object) -> None:
        """Store a reproducible configuration snapshot for the session.

        Args:
            config: Mapping, dataclass, or simple object that can be converted to
                JSON-compatible values.
        """

        self.config = _to_jsonable(config)
        logger.debug("Session config snapshot updated: %s", self.name)
        if self.path is not None and self.path.exists():
            self._write_config_snapshot()
            self.save_manifest()

    def start(
        self,
        *,
        include_environment: bool = True,
        env_keys: Sequence[str] = DEFAULT_ENV_KEYS,
    ) -> Path:
        """Start the session and create its reproducibility manifest.

        Args:
            include_environment: Capture Python, platform, environment variable,
                and Git metadata when True.
            env_keys: Environment variable names safe to copy into the manifest.

        Returns:
            Path to the session directory.

        Raises:
            RuntimeError: If the session is already started or finalized.
        """

        if self.state != SessionState.CREATED:
            raise RuntimeError(f"Cannot start session from state {self.state!s}.")

        if self.session_id is None:
            self.session_id = _build_session_id(self.name, self.created_at)

        session_path = self._require_path()
        session_path.mkdir(parents=True, exist_ok=False)
        (session_path / "results").mkdir()
        (session_path / "artifacts").mkdir()

        self.started_at = datetime.now(UTC)
        self.state = SessionState.RUNNING
        if include_environment:
            self.environment = capture_reproducibility_snapshot(env_keys=env_keys)

        self.record_event("session_started", {"simulate": self.simulate})
        self._write_config_snapshot()
        self.save_manifest()
        logger.info("Measurement session started: %s (%s)", self.name, session_path)
        return session_path

    def close(
        self,
        *,
        state: SessionState | str = SessionState.COMPLETED,
        metadata: Mapping[str, Any] | None = None,
    ) -> Path:
        """Finalize the session manifest.

        Args:
            state: Final session state.
            metadata: Optional metadata merged before final persistence.

        Returns:
            Path to the final manifest JSON file.

        Raises:
            RuntimeError: If the session has not been started.
        """

        if self.state not in {SessionState.RUNNING, SessionState.CREATED}:
            raise RuntimeError(f"Cannot close session from state {self.state!s}.")
        if self.path is None or not self.path.exists():
            raise RuntimeError("Cannot close a session before start().")

        if metadata is not None:
            self.metadata.update(_to_jsonable(metadata))

        final_state = SessionState(state)
        self.state = final_state
        self.ended_at = datetime.now(UTC)
        self.record_event("session_closed", {"state": final_state})
        manifest_path = self.save_manifest()
        logger.info("Measurement session closed: %s state=%s", self.name, final_state)
        return manifest_path

    def fail(
        self,
        error: BaseException | str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Path:
        """Finalize the session as failed and persist error details.

        Args:
            error: Exception or message that explains the failure.
            metadata: Optional metadata merged before final persistence.

        Returns:
            Path to the final manifest JSON file.
        """

        failure_metadata: dict[str, Any] = {"error": _to_jsonable(error)}
        if metadata is not None:
            failure_metadata.update(_to_jsonable(metadata))
        return self.close(state=SessionState.FAILED, metadata=failure_metadata)

    def record_event(
        self, name: str, metadata: Mapping[str, Any] | None = None
    ) -> SessionEvent:
        """Record a timestamped session event.

        Args:
            name: Event name.
            metadata: Optional event metadata.

        Returns:
            Event object appended to the session.
        """

        event = SessionEvent(name=name, metadata=_to_jsonable(metadata or {}))
        self.events.append(event)
        logger.debug("Session event recorded: %s", name)
        if self.path is not None and self.path.exists():
            self.save_manifest()
        return event

    def record_instrument(
        self,
        instrument: object,
        *,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Snapshot public instrument adapter identity and status.

        Args:
            instrument: Backend adapter or compatible object.
            name: Optional display name override.
            metadata: Optional extra instrument metadata.

        Returns:
            Manifest-ready instrument dictionary.
        """

        instrument_name = name or str(getattr(instrument, "name", type(instrument).__name__))
        status = _call_status(instrument)
        record: dict[str, Any] = {
            "name": instrument_name,
            "class": f"{type(instrument).__module__}.{type(instrument).__qualname__}",
            "resource": getattr(instrument, "resource", None),
            "simulate": bool(getattr(instrument, "simulate", False)),
            "status": status,
            "metadata": _to_jsonable(metadata or {}),
        }

        self.instruments = [
            existing
            for existing in self.instruments
            if existing.get("name") != instrument_name
        ]
        self.instruments.append(_to_jsonable(record))
        logger.debug("Session instrument snapshot recorded: %s", instrument_name)
        if self.path is not None and self.path.exists():
            self.save_manifest()
        return record

    def record_instruments(self, instruments: Iterable[object]) -> list[dict[str, Any]]:
        """Snapshot multiple instrument adapters.

        Args:
            instruments: Iterable of backend adapters or compatible objects.

        Returns:
            List of instrument records appended or updated in the manifest.
        """

        return [self.record_instrument(instrument) for instrument in instruments]

    def record_result(
        self,
        result: ExperimentResult,
        *,
        tag: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Path:
        """Persist an experiment result and register it as a session artifact.

        Args:
            result: Experiment result produced by ``BaseExperiment.run()``.
            tag: Optional filename label. Defaults to ``result.name``.
            metadata: Optional artifact-level metadata.

        Returns:
            Path to the written result JSON file.

        Raises:
            RuntimeError: If the session has not been started.
        """

        self._require_started()
        label = _slugify(tag or result.name)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        result_path = self._require_path() / "results" / f"{timestamp}_{label}.json"
        payload = {
            "session_id": self.session_id,
            "experiment": result.name,
            "state": result.state,
            "duration_s": result.duration_s,
            "error": result.error,
            "metadata": result.metadata,
            "data": result.data,
        }

        self._write_json(result_path, payload)
        artifact_metadata: dict[str, Any] = {
            "experiment": result.name,
            "state": result.state,
        }
        if metadata is not None:
            artifact_metadata.update(_to_jsonable(metadata))
        self.register_artifact(
            result_path,
            kind="experiment_result",
            name=result_path.name,
            metadata=artifact_metadata,
        )
        self.record_event(
            "experiment_result_recorded",
            {"experiment": result.name, "state": result.state, "path": result_path.name},
        )
        return result_path

    def write_json_artifact(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        kind: str = "artifact",
        subdir: str = "artifacts",
        metadata: Mapping[str, Any] | None = None,
    ) -> Path:
        """Write a structured JSON artifact inside the session directory.

        Args:
            name: File name. ``.json`` is appended when no suffix is present.
            payload: JSON-compatible payload to write.
            kind: Artifact category recorded in the manifest.
            subdir: Session subdirectory for the file.
            metadata: Optional artifact-level metadata.

        Returns:
            Path to the written JSON artifact.

        Raises:
            RuntimeError: If the session has not been started.
            ValueError: If ``name`` or ``subdir`` tries to escape the session.
        """

        self._require_started()
        safe_name = _safe_relative_file(name)
        if safe_name.suffix == "":
            safe_name = safe_name.with_suffix(".json")
        safe_subdir = _safe_relative_file(subdir)
        artifact_path = self._require_path() / safe_subdir / safe_name
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(artifact_path, payload)
        self.register_artifact(
            artifact_path,
            kind=kind,
            name=safe_name.name,
            metadata=metadata,
        )
        return artifact_path

    def register_artifact(
        self,
        path: Path | str,
        *,
        kind: str,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionArtifact:
        """Register an existing file in the session manifest.

        Args:
            path: File path to hash and register.
            kind: Artifact category.
            name: Optional display name. Defaults to the filename.
            metadata: Optional structured artifact metadata.

        Returns:
            Registered artifact metadata.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
            RuntimeError: If the session has not been started.
        """

        self._require_started()
        artifact_path = Path(path)
        if not artifact_path.exists():
            raise FileNotFoundError(artifact_path)

        session_path = self._require_path()
        try:
            manifest_path = artifact_path.relative_to(session_path).as_posix()
        except ValueError:
            manifest_path = artifact_path.resolve().as_posix()

        artifact = SessionArtifact(
            name=name or artifact_path.name,
            kind=kind,
            path=manifest_path,
            sha256=_sha256_file(artifact_path),
            size_bytes=artifact_path.stat().st_size,
            metadata=_to_jsonable(metadata or {}),
        )

        self.artifacts = [
            existing
            for existing in self.artifacts
            if not (existing.path == artifact.path and existing.kind == artifact.kind)
        ]
        self.artifacts.append(artifact)
        logger.debug("Session artifact registered: %s", artifact.path)
        self.save_manifest()
        return artifact

    def save_manifest(self) -> Path:
        """Write the session manifest to disk.

        Returns:
            Path to ``manifest.json``.

        Raises:
            RuntimeError: If the session has not been started.
        """

        session_path = self._require_path()
        if not session_path.exists():
            raise RuntimeError("Cannot save manifest before start().")

        payload = self.to_dict()
        manifest_path = session_path / "manifest.json"
        self._write_json(manifest_path, payload)
        self._write_yaml_manifest(session_path / "manifest.yaml", payload)
        return manifest_path

    def to_dict(self) -> dict[str, Any]:
        """Return the manifest dictionary for this session.

        Returns:
            JSON-serializable session manifest.
        """

        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "session_id": self.session_id,
            "name": self.name,
            "state": self.state,
            "simulate": self.simulate,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": self.duration_s,
            "metadata": self.metadata,
            "config": self.config,
            "environment": self.environment,
            "instruments": self.instruments,
            "events": [event.to_dict() for event in self.events],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    def __enter__(self) -> MeasurementSession:
        """Start the session for use as a context manager.

        Returns:
            Started session instance.
        """

        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Finalize the session on context-manager exit.

        Args:
            exc_type: Exception type raised inside the context, when present.
            exc_value: Exception raised inside the context, when present.
            traceback: Traceback raised inside the context, when present.
        """

        if exc_value is not None:
            self.fail(exc_value)
        else:
            self.close()

    @classmethod
    def from_manifest(cls, path: Path | str) -> MeasurementSession:
        """Load a session object from a manifest JSON file or session directory.

        Args:
            path: Path to ``manifest.json`` or to a session directory.

        Returns:
            Rehydrated measurement session.

        Raises:
            FileNotFoundError: If no manifest exists.
            ValueError: If the manifest schema is unsupported.
        """

        manifest_path = Path(path)
        if manifest_path.is_dir():
            manifest_path = manifest_path / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(manifest_path)

        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        schema_version = payload.get("schema_version")
        if schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"Unsupported session manifest schema: {schema_version!r}")

        session = cls(
            name=str(payload["name"]),
            created_at=_parse_datetime(payload["created_at"]),
            metadata=dict(payload.get("metadata", {})),
            root_dir=manifest_path.parent.parent,
            session_id=str(payload["session_id"]),
            simulate=bool(payload.get("simulate", False)),
            tags=list(payload.get("tags", [])),
        )
        session.state = SessionState(payload.get("state", SessionState.CREATED))
        session.started_at = _parse_optional_datetime(payload.get("started_at"))
        session.ended_at = _parse_optional_datetime(payload.get("ended_at"))
        session.config = payload.get("config")
        session.environment = dict(payload.get("environment", {}))
        session.instruments = list(payload.get("instruments", []))
        session.events = [
            SessionEvent(
                name=str(event["name"]),
                timestamp=_parse_datetime(event["timestamp"]),
                metadata=dict(event.get("metadata", {})),
            )
            for event in payload.get("events", [])
        ]
        session.artifacts = [
            SessionArtifact(
                name=str(artifact["name"]),
                kind=str(artifact["kind"]),
                path=str(artifact["path"]),
                sha256=str(artifact["sha256"]),
                size_bytes=int(artifact["size_bytes"]),
                created_at=_parse_datetime(artifact["created_at"]),
                metadata=dict(artifact.get("metadata", {})),
            )
            for artifact in payload.get("artifacts", [])
        ]
        return session

    def _require_path(self) -> Path:
        if self.session_id is None:
            raise RuntimeError("Session id is not available before start().")
        return self.root_dir / self.session_id

    def _require_started(self) -> None:
        if self.path is None or not self.path.exists():
            raise RuntimeError("Session must be started before recording artifacts.")

    def _write_config_snapshot(self) -> None:
        if self.config is None or self.path is None or not self.path.exists():
            return

        config_json = self.path / "config.json"
        self._write_json(config_json, self.config)
        self._write_yaml_manifest(self.path / "config.yaml", self.config)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(
            json.dumps(_to_jsonable(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_yaml_manifest(self, path: Path, payload: Mapping[str, Any]) -> None:
        try:
            import yaml
        except Exception:
            logger.debug("PyYAML unavailable; skipping YAML session snapshot.")
            return

        path.write_text(
            yaml.safe_dump(
                _to_jsonable(payload),
                allow_unicode=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


@dataclass
class SessionManager:
    """Factory and lookup helper for OpenBench measurement sessions.

    Attributes:
        root_dir: Directory containing all session directories.
        default_metadata: Metadata merged into every session created by this
            manager.
    """

    root_dir: Path | str = DEFAULT_SESSION_ROOT
    default_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize constructor inputs."""

        self.root_dir = Path(self.root_dir)
        self.default_metadata = dict(self.default_metadata)

    def create_session(
        self,
        name: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | object | None = None,
        simulate: bool = False,
        tags: Sequence[str] | None = None,
        include_environment: bool = True,
    ) -> MeasurementSession:
        """Create and start a new measurement session.

        Args:
            name: Human-readable session name.
            metadata: Optional session metadata.
            config: Optional reproducible configuration snapshot.
            simulate: Whether the session is a simulation run.
            tags: Optional labels for filtering.
            include_environment: Capture runtime environment metadata when True.

        Returns:
            Started ``MeasurementSession``.
        """

        merged_metadata = dict(self.default_metadata)
        if metadata is not None:
            merged_metadata.update(_to_jsonable(metadata))

        session = MeasurementSession(
            name=name,
            metadata=merged_metadata,
            root_dir=self.root_dir,
            simulate=simulate,
            tags=list(tags or []),
        )
        if config is not None:
            session.set_config(config)
        session.start(include_environment=include_environment)
        return session

    def load_session(self, path: Path | str) -> MeasurementSession:
        """Load a session from a manifest or session directory.

        Args:
            path: Manifest JSON path or session directory.

        Returns:
            Rehydrated ``MeasurementSession``.
        """

        return MeasurementSession.from_manifest(path)

    def list_sessions(self) -> list[Path]:
        """List known session directories sorted by name.

        Returns:
            Session directories that contain ``manifest.json``.
        """

        if not self.root_dir.exists():
            return []
        return sorted(
            path
            for path in self.root_dir.iterdir()
            if path.is_dir() and (path / "manifest.json").exists()
        )

    def latest_session(self) -> MeasurementSession | None:
        """Return the newest known session, if one exists.

        Returns:
            Latest session by directory modification time, or ``None``.
        """

        sessions = self.list_sessions()
        if not sessions:
            return None
        latest = max(sessions, key=lambda path: path.stat().st_mtime)
        return self.load_session(latest)


def capture_reproducibility_snapshot(
    *,
    env_keys: Sequence[str] = DEFAULT_ENV_KEYS,
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    """Capture runtime metadata needed to reproduce a measurement session.

    Args:
        env_keys: Environment variable names safe to copy into the manifest.
        cwd: Directory used for Git metadata discovery. Defaults to the current
            process directory.

    Returns:
        JSON-serializable environment snapshot.
    """

    working_dir = Path(cwd or os.getcwd())
    snapshot: dict[str, Any] = {
        "captured_at": datetime.now(UTC),
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "cwd": str(working_dir),
        "environment": {
            key: os.environ[key]
            for key in env_keys
            if key in os.environ and os.environ[key]
        },
        "git": _git_snapshot(working_dir),
    }

    return _to_jsonable(snapshot)


def _call_status(instrument: object) -> Any:
    status_attr = getattr(instrument, "status", None)
    if callable(status_attr):
        try:
            return _to_jsonable(status_attr())
        except Exception as exc:
            logger.debug("Instrument status snapshot failed: %s", exc)
            return {"error": str(exc)}
    return _to_jsonable(status_attr)


def _git_snapshot(cwd: Path) -> dict[str, Any]:
    root = _git_command(["rev-parse", "--show-toplevel"], cwd)
    if root is None:
        return {}

    status_lines = _git_command(["status", "--short"], cwd, split_lines=True) or []
    return {
        "root": root,
        "commit": _git_command(["rev-parse", "HEAD"], cwd),
        "branch": _git_command(["branch", "--show-current"], cwd),
        "dirty": bool(status_lines),
        "status": status_lines,
    }


def _git_command(args: list[str], cwd: Path, *, split_lines: bool = False) -> Any:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return [] if split_lines else None

    output = result.stdout.strip()
    if split_lines:
        return output.splitlines() if output else []
    return output


def _build_session_id(name: str, created_at: datetime) -> str:
    timestamp = _ensure_utc(created_at).strftime("%Y%m%dT%H%M%SZ")
    slug = _slugify(name)
    return f"{timestamp}_{slug}_{uuid4().hex[:8]}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug.lower() or "session"


def _safe_relative_file(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Path must stay inside the session directory: {value!r}")
    if str(path) in {"", "."}:
        raise ValueError("Artifact path must not be empty.")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _ensure_utc(parsed)


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(value)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _ensure_utc(value).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, bytes | bytearray):
        return value.hex()
    if value is None or isinstance(value, bool | int | float | str):
        return value
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return _to_jsonable(tolist())
        except Exception:
            logger.debug("tolist() conversion failed for %s", type(value).__name__)
    if hasattr(value, "__dict__"):
        public_items = {
            key: item for key, item in vars(value).items() if not key.startswith("_")
        }
        return _to_jsonable(public_items)
    return repr(value)


__all__ = [
    "MeasurementSession",
    "SessionArtifact",
    "SessionEvent",
    "SessionManager",
    "SessionState",
    "capture_reproducibility_snapshot",
]

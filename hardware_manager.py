"""Vendor-neutral hardware orchestration for GutVibe wellness kiosks.

Business modules depend on :class:`HardwareManager`, never on a device SDK.
Platform adapters (Raspberry Pi, Windows, Linux, or Android) implement the
small provider protocols below and can be replaced independently.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DeviceState(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeviceHealth:
    status: DeviceState
    message: str = ""
    battery_percent: float | None = None
    checked_at: str = field(default_factory=utc_now)
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhotoCapture:
    image: bytes
    media_type: str = "image/jpeg"
    captured_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class FaceDetection:
    face_count: int
    confidence: float


@dataclass(frozen=True)
class QualityResult:
    acceptable: bool
    score: float
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class Measurement:
    value: float
    unit: str
    stable: bool = True
    measured_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class CalibrationResult:
    successful: bool
    reference_value: float
    unit: str
    notes: str = ""


@dataclass(frozen=True)
class DiagnosticResult:
    successful: bool
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class BaseProvider(Protocol):
    def health(self) -> DeviceHealth: ...
    def restart(self) -> bool: ...
    def diagnose(self) -> DiagnosticResult: ...


@runtime_checkable
class CameraProvider(BaseProvider, Protocol):
    def capture_photo(self) -> PhotoCapture: ...
    def detect_faces(self, photo: PhotoCapture) -> FaceDetection: ...
    def check_quality(self, photo: PhotoCapture) -> QualityResult: ...


@runtime_checkable
class HeightSensorProvider(BaseProvider, Protocol):
    def read_height(self) -> Measurement: ...
    def calibrate(self, reference_height: float, unit: str = "cm") -> CalibrationResult: ...


@runtime_checkable
class WeightScaleProvider(BaseProvider, Protocol):
    def read_weight(self) -> Measurement: ...
    def wait_for_stable_weight(self, timeout_seconds: float = 10) -> Measurement: ...
    def calibrate(self, reference_weight: float, unit: str = "kg") -> CalibrationResult: ...


@runtime_checkable
class ThermalPrinterProvider(BaseProvider, Protocol):
    def print_wellness_summary(self, summary: Mapping[str, Any]) -> str: ...
    def print_qr_code(self, qr_payload: bytes, caption: str = "") -> str: ...
    def print_consent_receipt(self, consent: Mapping[str, Any]) -> str: ...


@runtime_checkable
class QRCodeProvider(BaseProvider, Protocol):
    def generate_report_download(self, report_url: str, expires_at: str | None = None) -> bytes: ...
    def generate_patient_visit(self, visit_id: str, patient_reference: str) -> bytes: ...


@runtime_checkable
class SpeakerProvider(BaseProvider, Protocol):
    def play(self, audio: bytes, media_type: str = "audio/wav") -> None: ...


@runtime_checkable
class MicrophoneProvider(BaseProvider, Protocol):
    def record(self, duration_seconds: float, sample_rate: int = 16000) -> bytes: ...


@runtime_checkable
class PaymentProvider(BaseProvider, Protocol):
    """Future payment boundary. Implementations must remain disabled for now."""

    def capabilities(self) -> Sequence[str]: ...
    def create_payment_intent(self, amount_minor: int, currency: str) -> str: ...


@runtime_checkable
class NetworkProvider(BaseProvider, Protocol):
    def is_connected(self) -> bool: ...
    def connection_type(self) -> str: ...


class HardwareError(RuntimeError):
    """Normalized provider failure safe for application-level handling."""

    def __init__(self, device: str, operation: str, message: str):
        super().__init__(f"{device} {operation} failed: {message}")
        self.device, self.operation = device, operation


class PaymentNotEnabledError(HardwareError):
    pass


PROVIDER_TYPES = {
    "camera": CameraProvider,
    "height_sensor": HeightSensorProvider,
    "weight_scale": WeightScaleProvider,
    "thermal_printer": ThermalPrinterProvider,
    "qr_code": QRCodeProvider,
    "speaker": SpeakerProvider,
    "microphone": MicrophoneProvider,
    "payment": PaymentProvider,
    "network": NetworkProvider,
}


class HardwareStore:
    """SQLite inventory, audit, calibration, and health repository."""

    def __init__(self, database: str | Path = "gutvibe_patients.db"):
        self.database = str(database)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS hardware_devices (
                    device_key TEXT PRIMARY KEY, device_type TEXT NOT NULL,
                    provider_name TEXT NOT NULL, vendor TEXT, model TEXT,
                    serial_number TEXT, enabled INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hardware_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, device_key TEXT NOT NULL,
                    level TEXT NOT NULL, operation TEXT NOT NULL, message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calibration_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, device_key TEXT NOT NULL,
                    reference_value REAL NOT NULL, unit TEXT NOT NULL,
                    successful INTEGER NOT NULL, notes TEXT, calibrated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_health (
                    device_key TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT,
                    battery_percent REAL, details_json TEXT NOT NULL DEFAULT '{}',
                    checked_at TEXT NOT NULL
                );
            """)

    def register(self, key: str, provider: Any, metadata: Mapping[str, Any]) -> None:
        values = dict(metadata)
        with self.connect() as conn:
            conn.execute("""INSERT INTO hardware_devices
                (device_key, device_type, provider_name, vendor, model, serial_number,
                 enabled, metadata_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_key) DO UPDATE SET provider_name=excluded.provider_name,
                vendor=excluded.vendor, model=excluded.model, serial_number=excluded.serial_number,
                enabled=excluded.enabled, metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at""", (
                key, key, type(provider).__name__, values.pop("vendor", None),
                values.pop("model", None), values.pop("serial_number", None),
                int(values.pop("enabled", True)), json.dumps(values, default=str), utc_now()))

    def log(self, key: str, level: str, operation: str, message: str,
            details: Mapping[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO hardware_logs (device_key, level, operation, message, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (key, level, operation, message, json.dumps(details or {}, default=str), utc_now()))

    def save_health(self, key: str, health: DeviceHealth) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO device_health VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_key) DO UPDATE SET status=excluded.status,
                message=excluded.message, battery_percent=excluded.battery_percent,
                details_json=excluded.details_json, checked_at=excluded.checked_at""",
                (key, health.status.value, health.message, health.battery_percent,
                 json.dumps(health.details, default=str), health.checked_at))

    def save_calibration(self, key: str, result: CalibrationResult) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO calibration_history (device_key, reference_value, unit, successful, notes, calibrated_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (key, result.reference_value, result.unit, int(result.successful), result.notes, utc_now()))

    def rows(self, table: str, limit: int = 100) -> list[dict[str, Any]]:
        if table not in {"hardware_devices", "hardware_logs", "calibration_history", "device_health"}:
            raise ValueError("Unsupported hardware table")
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (limit,))]


class HardwareManager:
    """Provider registry and the only hardware facade used by application code."""

    def __init__(self, store: HardwareStore | None = None):
        self.store = store or HardwareStore()
        self.providers: dict[str, BaseProvider] = {}

    def register(self, device_type: str, provider: BaseProvider, **metadata: Any) -> None:
        expected = PROVIDER_TYPES.get(device_type)
        if expected is None:
            raise ValueError(f"Unknown device type: {device_type}")
        if not isinstance(provider, expected):
            raise TypeError(f"Provider does not implement {expected.__name__}")
        self.providers[device_type] = provider
        self.store.register(device_type, provider, metadata)
        self.store.log(device_type, "INFO", "register", "Provider registered")

    def provider(self, device_type: str) -> BaseProvider:
        try:
            return self.providers[device_type]
        except KeyError as exc:
            raise HardwareError(device_type, "lookup", "no provider is configured") from exc

    def execute(self, device_type: str, operation: str, *args: Any, **kwargs: Any) -> Any:
        provider = self.provider(device_type)
        try:
            result = getattr(provider, operation)(*args, **kwargs)
            self.store.log(device_type, "INFO", operation, "Operation completed")
            return result
        except Exception as exc:
            self.store.log(device_type, "ERROR", operation, str(exc))
            if isinstance(exc, HardwareError):
                raise
            raise HardwareError(device_type, operation, str(exc)) from exc

    def capture_patient_photo(self) -> tuple[PhotoCapture, FaceDetection, QualityResult]:
        photo = self.execute("camera", "capture_photo")
        detection = self.execute("camera", "detect_faces", photo)
        quality = self.execute("camera", "check_quality", photo)
        if detection.face_count != 1 or not quality.acceptable:
            raise HardwareError("camera", "capture", "exactly one high-quality face is required")
        return photo, detection, quality

    def read_height(self) -> Measurement:
        return self.execute("height_sensor", "read_height")

    def read_stable_weight(self, timeout_seconds: float = 10) -> Measurement:
        measurement = self.execute("weight_scale", "wait_for_stable_weight", timeout_seconds)
        if not measurement.stable:
            raise HardwareError("weight_scale", "measurement", "measurement did not stabilize")
        return measurement

    def calibrate(self, device_type: str, reference: float, unit: str) -> CalibrationResult:
        if device_type not in {"height_sensor", "weight_scale"}:
            raise ValueError("Only height and weight devices support calibration")
        method = "calibrate"
        result = self.execute(device_type, method, reference, unit)
        self.store.save_calibration(device_type, result)
        return result

    def health_check(self) -> dict[str, DeviceHealth]:
        result: dict[str, DeviceHealth] = {}
        for key, provider in self.providers.items():
            try:
                health = provider.health()
            except Exception as exc:
                health = DeviceHealth(DeviceState.OFFLINE, str(exc))
                self.store.log(key, "ERROR", "health", str(exc))
            self.store.save_health(key, health)
            result[key] = health
        return result

    def diagnose(self, device_type: str) -> DiagnosticResult:
        return self.execute(device_type, "diagnose")

    def restart(self, device_type: str) -> bool:
        return bool(self.execute(device_type, "restart"))

    def create_payment_intent(self, *_: Any, **__: Any) -> str:
        """Explicitly prevent real payment processing during this phase."""
        raise PaymentNotEnabledError("payment", "create_payment_intent", "payments are not enabled")


def render_hardware_dashboard(manager: HardwareManager) -> None:
    """Render inventory, health, calibration, diagnostics, and logs in Streamlit."""
    import streamlit as st

    st.header("🛠️ Hardware Management")
    if st.button("Run device health check", use_container_width=True):
        manager.health_check()
    health = manager.store.rows("device_health")
    required = ("camera", "thermal_printer", "weight_scale", "height_sensor", "network", "ups")
    by_key = {row["device_key"]: row for row in health}
    cols = st.columns(3)
    for index, key in enumerate(required):
        row = by_key.get(key, {})
        label = "Battery / UPS" if key == "ups" else key.replace("_", " ").title()
        cols[index % 3].metric(label, row.get("status", "not configured"), row.get("checked_at", "Never"))
    st.caption(f"Last Device Check: {max((r['checked_at'] for r in health), default='Never')}")

    inventory, calibration, diagnostics, logs = st.tabs(
        ["Device Management", "Calibration", "Restart & Diagnostics", "Error Logs"])
    with inventory:
        st.dataframe(manager.store.rows("hardware_devices"), use_container_width=True)
    with calibration:
        device = st.selectbox("Device", ["height_sensor", "weight_scale"])
        reference = st.number_input("Reference value", min_value=0.01)
        unit = st.text_input("Unit", "cm" if device == "height_sensor" else "kg")
        if st.button("Calibrate"):
            try:
                result = manager.calibrate(device, reference, unit)
                st.success(result.notes or "Calibration completed")
            except HardwareError as exc:
                st.error(str(exc))
        st.dataframe(manager.store.rows("calibration_history"), use_container_width=True)
    with diagnostics:
        choices = list(manager.providers)
        if choices:
            selected = st.selectbox("Configured device", choices)
            left, right = st.columns(2)
            if left.button("Run diagnostics"):
                result = manager.diagnose(selected)
                (st.success if result.successful else st.error)(result.message)
            if right.button("Restart device"):
                st.success("Restart requested" if manager.restart(selected) else "Restart was not accepted")
        else:
            st.info("No hardware providers configured. Install platform adapters to manage devices.")
    with logs:
        all_logs = manager.store.rows("hardware_logs", 500)
        errors = [row for row in all_logs if row["level"] == "ERROR"]
        st.dataframe(errors, use_container_width=True)

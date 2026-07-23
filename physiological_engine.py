"""Provider-neutral physiological wellness signal engine.

The engine persists numerical estimates and provenance only.  It does not
diagnose, classify disease, or produce medical conclusions.
"""

from __future__ import annotations

import sqlite3
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import streamlit as st

DATABASE_FILE = "gutvibe_patients.db"
DISCLAIMER = "Wellness Estimate Only – Not a Medical Diagnosis."


def _bounded(value: float, name: str) -> float:
    value = float(value)
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


@dataclass(frozen=True)
class HeartRate:
    bpm: float


@dataclass(frozen=True)
class RespiratoryRate:
    breaths_per_minute: float


@dataclass(frozen=True)
class HeartRateVariability:
    """Placeholder estimate; RMSSD is expressed in milliseconds."""

    rmssd_ms: float | None = None


@dataclass(frozen=True)
class SignalQuality:
    score: float
    label: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _bounded(self.score, "signal quality"))


@dataclass(frozen=True)
class BiologicalAge:
    """Non-clinical wellness estimate placeholder."""

    years: float | None = None


@dataclass(frozen=True)
class StressIndex:
    """Non-clinical wellness indicator placeholder (0–100)."""

    score: float | None = None


@dataclass(frozen=True)
class PhysiologicalResult:
    heart_rate: HeartRate | None
    respiratory_rate: RespiratoryRate | None
    hrv: HeartRateVariability
    signal_quality: SignalQuality
    biological_age: BiologicalAge
    stress_index: StressIndex
    confidence: float
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _bounded(self.confidence, "confidence"))


class SignalProvider(ABC):
    """Contract for any algorithm or device that extracts wellness signals."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def extract(self, payload: Any) -> PhysiologicalResult: ...


class CameraSignalProvider(SignalProvider, ABC):
    """Interface for providers accepting frames or a captured video."""

    @abstractmethod
    def extract_from_frames(self, frames: Sequence[Any]) -> PhysiologicalResult: ...

    def extract(self, payload: Any) -> PhysiologicalResult:
        return self.extract_from_frames(payload)


class SensorSignalProvider(SignalProvider, ABC):
    """Interface for Bluetooth, watch, and medical-sensor adapters."""

    @abstractmethod
    def extract_from_readings(self, readings: Mapping[str, Any]) -> PhysiologicalResult: ...

    def extract(self, payload: Any) -> PhysiologicalResult:
        return self.extract_from_readings(payload)


class FutureRPPGProvider(CameraSignalProvider):
    """Explicit extension point for a future, validated camera rPPG algorithm."""

    name = "future-rppg"

    def extract_from_frames(self, frames: Sequence[Any]) -> PhysiologicalResult:
        raise NotImplementedError("Configure a validated rPPG provider before camera analysis")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_connection(database_file: str = DATABASE_FILE) -> sqlite3.Connection:
    path = Path(database_file)
    path.touch(exist_ok=True)
    path.chmod(0o600)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS physiological_measurements (
            measurement_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL,
            measured_at TEXT NOT NULL, measurement_source TEXT NOT NULL,
            heart_rate_bpm REAL, respiratory_rate_bpm REAL, hrv_rmssd_ms REAL,
            confidence_score REAL NOT NULL CHECK(confidence_score BETWEEN 0 AND 1)
        );
        CREATE TABLE IF NOT EXISTS signal_quality (
            measurement_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL,
            measured_at TEXT NOT NULL, score REAL NOT NULL CHECK(score BETWEEN 0 AND 1),
            quality_label TEXT NOT NULL,
            FOREIGN KEY(measurement_id) REFERENCES physiological_measurements(measurement_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS wellness_scores (
            measurement_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL,
            measured_at TEXT NOT NULL, biological_age_years REAL, stress_index REAL,
            disclaimer TEXT NOT NULL,
            FOREIGN KEY(measurement_id) REFERENCES physiological_measurements(measurement_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_physiological_patient_time
            ON physiological_measurements(patient_id, measured_at DESC);
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    return conn


class PhysiologicalEngine:
    """Orchestrates replaceable providers and persistence independently of UI."""

    def __init__(self, database_file: str = DATABASE_FILE) -> None:
        self.database_file = database_file
        self.providers: dict[str, SignalProvider] = {}
        with get_connection(database_file):
            pass

    def register_provider(self, provider: SignalProvider) -> None:
        if not provider.name.strip():
            raise ValueError("provider name is required")
        self.providers[provider.name] = provider

    def analyze(self, patient_id: str, provider_name: str, payload: Any,
                measured_at: str | None = None) -> str:
        if not patient_id.strip():
            raise ValueError("patient_id is required")
        try:
            provider = self.providers[provider_name]
        except KeyError as exc:
            raise ValueError(f"Unknown signal provider: {provider_name}") from exc
        return self.store(patient_id, provider.name, provider.extract(payload), measured_at)

    # These entry points intentionally share the same adapter contract.
    def analyze_camera(self, patient_id: str, provider_name: str, frames: Sequence[Any]) -> str:
        return self.analyze(patient_id, provider_name, frames)

    def analyze_bluetooth(self, patient_id: str, provider_name: str, readings: Mapping[str, Any]) -> str:
        return self.analyze(patient_id, provider_name, readings)

    analyze_smart_watch = analyze_bluetooth
    analyze_medical_sensor = analyze_bluetooth

    def store(self, patient_id: str, source: str, result: PhysiologicalResult,
              measured_at: str | None = None) -> str:
        measurement_id = f"PHY-{uuid.uuid4().hex[:12].upper()}"
        timestamp = measured_at or _utc_now()
        with get_connection(self.database_file) as conn:
            conn.execute("""INSERT INTO physiological_measurements VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                measurement_id, patient_id, timestamp, source,
                result.heart_rate.bpm if result.heart_rate else None,
                result.respiratory_rate.breaths_per_minute if result.respiratory_rate else None,
                result.hrv.rmssd_ms, result.confidence,
            ))
            conn.execute("INSERT INTO signal_quality VALUES (?, ?, ?, ?, ?)", (
                measurement_id, patient_id, timestamp, result.signal_quality.score,
                result.signal_quality.label,
            ))
            conn.execute("INSERT INTO wellness_scores VALUES (?, ?, ?, ?, ?, ?)", (
                measurement_id, patient_id, timestamp, result.biological_age.years,
                result.stress_index.score, DISCLAIMER,
            ))
            conn.commit()
        return measurement_id

    def history(self, patient_id: str, limit: int = 100) -> pd.DataFrame:
        if limit < 1:
            raise ValueError("limit must be positive")
        with get_connection(self.database_file) as conn:
            return pd.read_sql_query("""
                SELECT p.measurement_id, p.measured_at, p.measurement_source,
                       p.heart_rate_bpm, p.respiratory_rate_bpm, p.hrv_rmssd_ms,
                       p.confidence_score, q.score AS signal_quality,
                       q.quality_label, w.biological_age_years, w.stress_index
                FROM physiological_measurements p
                JOIN signal_quality q USING (measurement_id)
                JOIN wellness_scores w USING (measurement_id)
                WHERE p.patient_id = ? ORDER BY p.measured_at DESC LIMIT ?
            """, conn, params=(patient_id, limit))


def wellness_summary(patient_id: str, database_file: str = DATABASE_FILE) -> dict[str, Any] | None:
    history = PhysiologicalEngine(database_file).history(patient_id, 1)
    return None if history.empty else history.iloc[0].to_dict()


def render_physiological_dashboard(patient_id: str, database_file: str = DATABASE_FILE) -> None:
    """Render a read-only Streamlit dashboard for one registered patient."""
    st.warning(DISCLAIMER)
    history = PhysiologicalEngine(database_file).history(patient_id)
    if history.empty:
        st.info("No physiological wellness estimates are available for this patient.")
        return
    latest = history.iloc[0]
    columns = st.columns(3)
    columns[0].metric("Heart Rate", f"{latest['heart_rate_bpm']:.0f} bpm" if pd.notna(latest["heart_rate_bpm"]) else "—")
    columns[1].metric("Respiratory Rate", f"{latest['respiratory_rate_bpm']:.1f} breaths/min" if pd.notna(latest["respiratory_rate_bpm"]) else "—")
    columns[2].metric("Signal Quality", f"{latest['signal_quality']:.0%} ({latest['quality_label']})")
    st.subheader("Wellness Trend")
    trend = history.sort_values("measured_at").set_index("measured_at")
    st.line_chart(trend[["heart_rate_bpm", "respiratory_rate_bpm", "stress_index"]])
    st.subheader("Historical Measurements")
    st.dataframe(history, use_container_width=True, hide_index=True)


__all__ = [name for name in (
    "HeartRate", "RespiratoryRate", "HeartRateVariability", "SignalQuality",
    "BiologicalAge", "StressIndex", "PhysiologicalResult", "SignalProvider",
    "CameraSignalProvider", "SensorSignalProvider", "FutureRPPGProvider",
    "PhysiologicalEngine", "render_physiological_dashboard", "wellness_summary",
    "DISCLAIMER",
)]

"""Transparent, non-diagnostic GutVibe wellness scoring.

Rules in this module are deliberately simple and replaceable.  A score describes
the available wellness observations; it must never be used to diagnose or predict
disease, or as a substitute for clinician judgement.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

DATABASE_FILE = "gutvibe_patients.db"
DISCLAIMER = "This is a Wellness Assessment and is NOT a Medical Diagnosis."


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class ComponentResult:
    name: str
    score: float | None
    confidence: float
    weight: float
    explanation: str
    improvement_area: str | None = None
    source: str = ""


@dataclass(frozen=True)
class WellnessAssessment:
    assessment_id: str
    patient_id: str
    assessed_at: str
    overall_score: float
    confidence_score: float
    trend_score: float
    data_completeness_score: float
    components: tuple[ComponentResult, ...]
    suggestions: tuple[str, ...]
    disclaimer: str = DISCLAIMER


class ComponentRule:
    """Small adapter that makes every initial rule independently replaceable."""

    def __init__(self, name: str, weight: float,
                 scorer: Callable[[Mapping[str, Any]], ComponentResult]) -> None:
        self.name, self.weight, self.scorer = name, weight, scorer

    def evaluate(self, data: Mapping[str, Any]) -> ComponentResult:
        return self.scorer(data)


def _missing(name: str, weight: float, source: str) -> ComponentResult:
    return ComponentResult(name, None, 0.0, weight,
                           f"Not scored: no usable {source} data are available.", source=source)


def _face(data: Mapping[str, Any]) -> ComponentResult:
    weight = .15
    scan, landmarks = data.get("face_scan"), data.get("face_landmarks")
    if not scan:
        return _missing("Face Wellness", weight, "face scan")
    score = 70.0 + (15.0 if landmarks else 0.0)
    explanation = "70 points for a valid single-face scan"
    if landmarks:
        explanation += " + 15 points for completed landmark measurements"
    explanation += "; visual measurements are not interpreted medically."
    return ComponentResult("Face Wellness", score, .75 if landmarks else .55, weight,
                           explanation, None if landmarks else "Complete face landmark analysis.", "face scan and landmarks")


def _skin(data: Mapping[str, Any]) -> ComponentResult:
    weight = .15
    skin = data.get("skin")
    if not skin:
        return _missing("Skin Wellness", weight, "skin analysis")
    uniformity = skin.get("skin_uniformity_score")
    if uniformity is None:
        return ComponentResult("Skin Wellness", 70, .45, weight,
                               "70-point neutral baseline because analysis exists but uniformity is unavailable.",
                               "Maintain general hydration and skin-care habits.", "skin analysis")
    raw = float(uniformity)
    score = clamp(raw * 100 if raw <= 1 else raw)
    return ComponentResult("Skin Wellness", score, .70, weight,
                           f"Skin uniformity contributes {score:.1f}/100 (100 means more even measured color); no condition is inferred.",
                           "Maintain general hydration and skin-care habits." if score < 65 else None, "skin analysis")


def _physiology(data: Mapping[str, Any]) -> ComponentResult:
    weight = .20
    p = data.get("physiology")
    if not p:
        return _missing("Physiological Wellness", weight, "physiological signal")
    parts: list[float] = []
    details: list[str] = []
    if p.get("heart_rate_bpm") is not None:
        hr = float(p["heart_rate_bpm"])
        value = clamp(100 - abs(hr - 70) * 2.0)
        parts.append(value); details.append(f"heart-rate observation {value:.1f}")
    if p.get("respiratory_rate_bpm") is not None:
        rr = float(p["respiratory_rate_bpm"])
        value = clamp(100 - abs(rr - 16) * 6.0)
        parts.append(value); details.append(f"respiratory-rate observation {value:.1f}")
    if p.get("stress_index") is not None:
        value = 100 - clamp(float(p["stress_index"]))
        parts.append(value); details.append(f"inverse stress indicator {value:.1f}")
    if not parts:
        return _missing("Physiological Wellness", weight, "usable physiological signal")
    quality = clamp(float(p.get("signal_quality", .5)), 0, 1)
    confidence = min(quality, clamp(float(p.get("confidence_score", .5)), 0, 1))
    score = sum(parts) / len(parts)
    return ComponentResult("Physiological Wellness", score, confidence, weight,
                           "Average of " + ", ".join(details) + "; these are non-clinical observations.",
                           "Practice general stress-management habits." if score < 65 else None, "physiological signal engine")


def _lifestyle(data: Mapping[str, Any]) -> ComponentResult:
    weight = .15
    patient = data.get("patient") or {}
    parts, details = [], []
    for key, label in (("circadian_score", "circadian"), ("gut_health_score", "self-reported gut wellness")):
        try:
            if str(patient.get(key, "")).strip():
                value = clamp(float(patient[key])); parts.append(value); details.append(f"{label} {value:.1f}")
        except (TypeError, ValueError):
            pass
    if not parts:
        return _missing("Lifestyle", weight, "lifestyle questionnaire")
    score = sum(parts) / len(parts)
    return ComponentResult("Lifestyle", score, .65, weight, "Average of " + " and ".join(details) + ".",
                           "Build consistent daily routines and stress-management habits." if score < 65 else None, "patient registration")


def _patient_score(name: str, key: str, weight: float, guidance: str) -> Callable[[Mapping[str, Any]], ComponentResult]:
    def score(data: Mapping[str, Any]) -> ComponentResult:
        raw = (data.get("patient") or {}).get(key)
        try:
            if raw is None or not str(raw).strip(): raise ValueError
            value = clamp(float(raw))
        except (TypeError, ValueError):
            return _missing(name, weight, name.lower() + " measurement (placeholder)")
        return ComponentResult(name, value, .60, weight, f"Uses the recorded {name.lower()} value directly: {value:.1f}/100.",
                               guidance if value < 65 else None, "patient registration placeholder")
    return score


DEFAULT_RULES = (
    ComponentRule("Face Wellness", .15, _face),
    ComponentRule("Skin Wellness", .15, _skin),
    ComponentRule("Physiological Wellness", .20, _physiology),
    ComponentRule("Lifestyle", .15, _lifestyle),
    ComponentRule("Nutrition", .15, _patient_score("Nutrition", "nutrition_score", .15, "Choose varied, balanced meals and adequate hydration.")),
    ComponentRule("Activity", .10, _patient_score("Activity", "activity_score", .10, "Increase physical activity gradually as appropriate for you.")),
    ComponentRule("Sleep", .10, _patient_score("Sleep", "sleep_score", .10, "Improve sleep consistency and allow adequate rest.")),
)


def get_connection(database_file: str = DATABASE_FILE) -> sqlite3.Connection:
    path = Path(database_file); path.touch(exist_ok=True); path.chmod(0o600)
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wellness_score (
            assessment_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, assessed_at TEXT NOT NULL,
            overall_score REAL NOT NULL, confidence_score REAL NOT NULL,
            trend_score REAL NOT NULL, data_completeness_score REAL NOT NULL,
            suggestions_json TEXT NOT NULL, disclaimer TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wellness_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id TEXT NOT NULL UNIQUE,
            patient_id TEXT NOT NULL, assessed_at TEXT NOT NULL, overall_score REAL NOT NULL,
            trend_score REAL NOT NULL,
            FOREIGN KEY(assessment_id) REFERENCES wellness_score(assessment_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS score_components (
            component_id INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id TEXT NOT NULL,
            component_name TEXT NOT NULL, score REAL, confidence REAL NOT NULL, weight REAL NOT NULL,
            weighted_contribution REAL NOT NULL, explanation TEXT NOT NULL,
            improvement_area TEXT, source TEXT NOT NULL,
            FOREIGN KEY(assessment_id) REFERENCES wellness_score(assessment_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wellness_history_patient ON wellness_history(patient_id, assessed_at DESC);
    """)
    conn.execute("PRAGMA foreign_keys = ON"); conn.commit(); return conn


class WellnessScoringEngine:
    def __init__(self, database_file: str = DATABASE_FILE, rules: tuple[ComponentRule, ...] = DEFAULT_RULES) -> None:
        self.database_file, self.rules = database_file, rules
        with get_connection(database_file): pass

    def assess(self, patient_id: str, data: Mapping[str, Any] | None = None,
               assessed_at: str | None = None, persist: bool = True) -> WellnessAssessment:
        if not patient_id.strip(): raise ValueError("patient_id is required")
        inputs = dict(data) if data is not None else self.load_inputs(patient_id)
        components = tuple(rule.evaluate(inputs) for rule in self.rules)
        available = [c for c in components if c.score is not None]
        available_weight = sum(c.weight for c in available)
        overall = sum(float(c.score) * c.weight for c in available) / available_weight if available_weight else 0.0
        confidence = (sum(c.confidence * c.weight for c in available) / available_weight * 100) if available_weight else 0.0
        completeness = sum(c.weight for c in available) / sum(c.weight for c in components) * 100
        previous = self.latest(patient_id)
        trend = 50.0 if previous is None else clamp(50 + (overall - previous.overall_score) * 2.5)
        suggestions = tuple(dict.fromkeys(c.improvement_area for c in components if c.improvement_area))
        assessment = WellnessAssessment(f"WELL-{uuid.uuid4().hex[:12].upper()}", patient_id,
            assessed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            round(overall, 1), round(confidence, 1), round(trend, 1), round(completeness, 1), components, suggestions)
        if persist: self.store(assessment)
        return assessment

    def load_inputs(self, patient_id: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        with get_connection(self.database_file) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "patients" in tables:
                row = conn.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
                result["patient"] = dict(row) if row else {}
            if "face_scans" in tables:
                row = conn.execute("SELECT * FROM face_scans WHERE patient_id=? ORDER BY captured_at DESC LIMIT 1", (patient_id,)).fetchone()
                result["face_scan"] = dict(row) if row else None
            if "face_landmark_measurements" in tables:
                row = conn.execute("SELECT * FROM face_landmark_measurements WHERE patient_id=? ORDER BY analyzed_at DESC LIMIT 1", (patient_id,)).fetchone()
                result["face_landmarks"] = dict(row) if row else None
            if "skin_color_measurements" in tables:
                row = conn.execute("SELECT measurements_json FROM skin_color_measurements WHERE patient_id=? ORDER BY analyzed_at DESC LIMIT 1", (patient_id,)).fetchone()
                result["skin"] = json.loads(row[0]) if row else None
            if "physiological_measurements" in tables:
                row = conn.execute("""SELECT p.*, q.score signal_quality, w.stress_index FROM physiological_measurements p
                    LEFT JOIN signal_quality q USING(measurement_id) LEFT JOIN wellness_scores w USING(measurement_id)
                    WHERE p.patient_id=? ORDER BY p.measured_at DESC LIMIT 1""", (patient_id,)).fetchone()
                result["physiology"] = dict(row) if row else None
        return result

    def store(self, assessment: WellnessAssessment) -> None:
        with get_connection(self.database_file) as conn:
            conn.execute("INSERT INTO wellness_score VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (assessment.assessment_id, assessment.patient_id, assessment.assessed_at, assessment.overall_score,
                 assessment.confidence_score, assessment.trend_score, assessment.data_completeness_score,
                 json.dumps(assessment.suggestions), assessment.disclaimer))
            conn.execute("INSERT INTO wellness_history (assessment_id, patient_id, assessed_at, overall_score, trend_score) VALUES (?, ?, ?, ?, ?)",
                (assessment.assessment_id, assessment.patient_id, assessment.assessed_at, assessment.overall_score, assessment.trend_score))
            available_weight = sum(c.weight for c in assessment.components if c.score is not None)
            conn.executemany("""INSERT INTO score_components (assessment_id, component_name, score, confidence, weight,
                weighted_contribution, explanation, improvement_area, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(assessment.assessment_id, c.name, c.score, c.confidence, c.weight,
                  (float(c.score) * c.weight / available_weight if c.score is not None and available_weight else 0),
                  c.explanation, c.improvement_area, c.source) for c in assessment.components])
            conn.commit()

    def latest(self, patient_id: str) -> WellnessAssessment | None:
        with get_connection(self.database_file) as conn:
            row = conn.execute("SELECT * FROM wellness_score WHERE patient_id=? ORDER BY assessed_at DESC, rowid DESC LIMIT 1", (patient_id,)).fetchone()
            if not row: return None
            components = tuple(ComponentResult(r["component_name"], r["score"], r["confidence"], r["weight"], r["explanation"], r["improvement_area"], r["source"])
                for r in conn.execute("SELECT * FROM score_components WHERE assessment_id=? ORDER BY component_id", (row["assessment_id"],)))
        return WellnessAssessment(row["assessment_id"], row["patient_id"], row["assessed_at"], row["overall_score"],
            row["confidence_score"], row["trend_score"], row["data_completeness_score"], components,
            tuple(json.loads(row["suggestions_json"])), row["disclaimer"])

    def history(self, patient_id: str) -> list[dict[str, Any]]:
        with get_connection(self.database_file) as conn:
            return [dict(r) for r in conn.execute("SELECT assessed_at, overall_score, trend_score FROM wellness_history WHERE patient_id=? ORDER BY assessed_at", (patient_id,))]


def assessment_summary(patient_id: str, database_file: str = DATABASE_FILE) -> dict[str, Any] | None:
    assessment = WellnessScoringEngine(database_file).latest(patient_id)
    if not assessment: return None
    return {"overall_score": assessment.overall_score, "confidence_score": assessment.confidence_score,
            "trend_score": assessment.trend_score, "data_completeness_score": assessment.data_completeness_score,
            "disclaimer": assessment.disclaimer}

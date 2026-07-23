"""Non-invasive platform evaluation and pilot-readiness framework.

Evaluators receive probes supplied by a test or deployment harness.  This module
never invokes clinical/business workflows by itself, so an evaluation cannot
create patients, send messages, or operate hardware accidentally.
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import statistics
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    SKIPPED = "skipped"
    ERROR = "error"


class Category(str, Enum):
    MODULE = "module"
    WORKFLOW = "workflow"
    HARDWARE = "hardware"
    SECURITY = "security"
    AI = "ai"
    PERFORMANCE = "performance"


MODULES = (
    "Patient Registration", "Face Scan", "Face Landmark Analysis", "Skin Analysis",
    "Physiological Engine", "AI Wellness Scoring", "Food as Medicine", "Voice Assistant",
    "WhatsApp CRM", "Doctor Referral", "Hardware Integration", "Security & Compliance",
)
HARDWARE = ("Camera", "Height Sensor", "Weight Scale", "Printer", "QR Code", "Speaker", "Microphone", "Network")
SECURITY = ("Login", "RBAC", "Encryption", "Audit Logs", "Consent Workflow", "Backup & Restore")
AI_CHECKS = ("Wellness Score calculation", "Confidence Score", "Missing data handling", "Signal Quality", "Recommendation generation")
WORKFLOW = ("Complete patient journey", "Report generation", "WhatsApp delivery", "Doctor referral", "Hardware communication")
LATENCY_METRICS = ("Startup time", "Report generation time", "Face analysis latency")


@dataclass(frozen=True)
class ProbeResult:
    status: Status
    message: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None


class Probe(Protocol):
    def __call__(self) -> bool | ProbeResult | Mapping[str, Any]: ...


@dataclass(frozen=True)
class EvaluationResult:
    result_id: str
    run_id: str
    category: str
    check_name: str
    status: str
    message: str
    evidence: Mapping[str, Any]
    duration_ms: float
    created_at: str


@dataclass(frozen=True)
class ClinicalValidation:
    check_name: str
    expert_reviewer: str
    clinician_comments: str
    predicted_value: float | str | None
    ground_truth_value: float | str | None
    validation_status: str
    reviewed_at: str = field(default_factory=utc_now)


class EvaluationStore:
    """SQLite persistence for runs, results, readiness and benchmarks."""

    def __init__(self, database: str | Path = "gutvibe_evaluation.db"):
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS validation_runs (
              run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
              environment TEXT NOT NULL, status TEXT NOT NULL, metadata_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evaluation_results (
              result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, category TEXT NOT NULL,
              check_name TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL,
              evidence_json TEXT NOT NULL, duration_ms REAL NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES validation_runs(run_id));
            CREATE TABLE IF NOT EXISTS pilot_readiness (
              run_id TEXT PRIMARY KEY, module_health REAL NOT NULL, test_results REAL NOT NULL,
              hardware_status REAL NOT NULL, security_status REAL NOT NULL,
              deployment_readiness REAL NOT NULL, overall_score REAL NOT NULL,
              blockers_json TEXT NOT NULL, calculated_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES validation_runs(run_id));
            CREATE TABLE IF NOT EXISTS performance_metrics (
              metric_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, metric_name TEXT NOT NULL,
              value REAL NOT NULL, unit TEXT NOT NULL, threshold REAL, passed INTEGER,
              measured_at TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES validation_runs(run_id));
            """)
        try:
            os.chmod(self.database, 0o600)
        except OSError:
            pass

    def start_run(self, metadata: Mapping[str, Any] | None = None) -> str:
        run_id = str(uuid.uuid4())
        environment = f"{platform.system()} / Python {platform.python_version()}"
        with self.connect() as conn:
            conn.execute("INSERT INTO validation_runs VALUES (?,?,NULL,?,'running',?)",
                         (run_id, utc_now(), environment, json.dumps(metadata or {}, default=str)))
        return run_id

    def save_result(self, result: EvaluationResult) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO evaluation_results VALUES (?,?,?,?,?,?,?,?,?)", (
                result.result_id, result.run_id, result.category, result.check_name,
                result.status, result.message, json.dumps(result.evidence, default=str),
                result.duration_ms, result.created_at))

    def save_metric(self, run_id: str, name: str, value: float, unit: str,
                    threshold: float | None = None, lower_is_better: bool = True) -> None:
        passed = None if threshold is None else int(value <= threshold if lower_is_better else value >= threshold)
        with self.connect() as conn:
            conn.execute("INSERT INTO performance_metrics VALUES (?,?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), run_id, name, value, unit, threshold, passed, utc_now()))

    def finish_run(self, run_id: str, status: str = "complete") -> None:
        with self.connect() as conn:
            conn.execute("UPDATE validation_runs SET completed_at=?,status=? WHERE run_id=?", (utc_now(), status, run_id))

    def results(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM evaluation_results WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        output = []
        for row in rows:
            item = dict(row); item["evidence"] = json.loads(item.pop("evidence_json")); output.append(item)
        return output


class EvaluationSuite:
    """Registry-driven runner; probes can be replaced without changing rules."""

    def __init__(self, store: EvaluationStore, probes: Mapping[str, Probe] | None = None):
        self.store = store
        self.probes: dict[str, Probe] = dict(probes or {})

    def register(self, check_name: str, probe: Probe) -> None:
        self.probes[check_name] = probe

    def _run_one(self, run_id: str, category: Category, name: str) -> EvaluationResult:
        started = time.perf_counter()
        probe = self.probes.get(name)
        if probe is None:
            outcome = ProbeResult(Status.SKIPPED, "No deployment probe configured")
        else:
            try:
                raw = probe()
                if isinstance(raw, ProbeResult): outcome = raw
                elif isinstance(raw, Mapping):
                    outcome = ProbeResult(Status(raw.get("status", "pass")), str(raw.get("message", "")), raw.get("evidence", {}))
                else: outcome = ProbeResult(Status.PASS if raw else Status.FAIL, "Probe returned true" if raw else "Probe returned false")
            except Exception as exc:  # probe failures are evidence, not suite failures
                outcome = ProbeResult(Status.ERROR, f"{type(exc).__name__}: {exc}")
        elapsed = outcome.duration_ms if outcome.duration_ms is not None else (time.perf_counter() - started) * 1000
        result = EvaluationResult(str(uuid.uuid4()), run_id, category.value, name, outcome.status.value,
                                  outcome.message, outcome.evidence, round(elapsed, 3), utc_now())
        self.store.save_result(result)
        return result

    def run(self, metadata: Mapping[str, Any] | None = None) -> tuple[str, list[EvaluationResult]]:
        run_id = self.store.start_run(metadata)
        groups = ((Category.MODULE, MODULES), (Category.WORKFLOW, WORKFLOW),
                  (Category.HARDWARE, HARDWARE), (Category.SECURITY, SECURITY), (Category.AI, AI_CHECKS))
        results = [self._run_one(run_id, category, name) for category, names in groups for name in names]
        self.store.finish_run(run_id, "complete")
        return run_id, results

    def benchmark(self, run_id: str, name: str, operation: Callable[[], Any], *,
                  repeats: int = 3, threshold: float | None = None) -> float:
        if repeats < 1: raise ValueError("repeats must be positive")
        samples = []
        for _ in range(repeats):
            start = time.perf_counter(); operation(); samples.append((time.perf_counter() - start) * 1000)
        value = statistics.median(samples)
        self.store.save_metric(run_id, name, value, "ms", threshold)
        return value

    def run_performance_checks(self, run_id: str, operations: Mapping[str, Callable[[], Any]], *,
                               thresholds_ms: Mapping[str, float] | None = None,
                               repeats: int = 3) -> dict[str, float]:
        """Benchmark the three required latency operations when configured."""
        thresholds_ms = thresholds_ms or {}
        return {name: self.benchmark(run_id, name, operations[name], repeats=repeats,
                                     threshold=thresholds_ms.get(name))
                for name in LATENCY_METRICS if name in operations}

    @contextmanager
    def resource_benchmark(self, run_id: str):
        """Measure wall time, peak traced memory and process CPU without third-party tools."""
        import tracemalloc
        tracemalloc.start(); wall = time.perf_counter(); cpu = time.process_time()
        try: yield
        finally:
            cpu_ms = (time.process_time() - cpu) * 1000
            wall_ms = (time.perf_counter() - wall) * 1000
            _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
            self.store.save_metric(run_id, "CPU usage", cpu_ms, "cpu_ms")
            self.store.save_metric(run_id, "Memory usage", peak / (1024 * 1024), "MiB")
            self.store.save_metric(run_id, "Startup time", wall_ms, "ms")


class PilotReadinessCalculator:
    WEIGHTS = {"module": .25, "tests": .20, "hardware": .20, "security": .20, "deployment": .15}

    def __init__(self, store: EvaluationStore): self.store = store

    @staticmethod
    def _score(rows: Iterable[Mapping[str, Any]]) -> float:
        values = list(rows)
        if not values: return 0.0
        points = {"pass": 1.0, "warning": .5, "skipped": 0.0, "fail": 0.0, "error": 0.0}
        return round(100 * sum(points.get(str(row["status"]), 0) for row in values) / len(values), 1)

    def calculate(self, run_id: str) -> dict[str, Any]:
        rows = self.store.results(run_id)
        by = lambda category: [r for r in rows if r["category"] == category]
        scores = {"module": self._score(by("module")), "tests": self._score(rows),
                  "hardware": self._score(by("hardware")), "security": self._score(by("security"))}
        critical = [r["check_name"] for r in rows if r["status"] in {"fail", "error", "skipped"}]
        scores["deployment"] = 100.0 if not critical else max(0.0, 100 - len(critical) * 5)
        overall = round(sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS), 1)
        payload = {"module_health": scores["module"], "test_results": scores["tests"],
                   "hardware_status": scores["hardware"], "security_status": scores["security"],
                   "deployment_readiness": scores["deployment"], "overall_score": overall,
                   "blockers": critical, "ready": overall >= 85 and not critical}
        with self.store.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO pilot_readiness VALUES (?,?,?,?,?,?,?,?,?)",
                         (run_id, *[payload[k] for k in ("module_health", "test_results", "hardware_status", "security_status", "deployment_readiness", "overall_score")], json.dumps(critical), utc_now()))
        return payload


VALIDATION_CHECKLIST = (
    "All automated checks reviewed", "Clinical ground truth reviewed by an authorized clinician",
    "Known limitations documented", "Failed and skipped checks have owners", "Evidence retained under approved policy",
)
DEPLOYMENT_CHECKLIST = (
    "Production probes all pass", "Required hardware calibrated", "Secrets supplied by approved key manager",
    "Backup restore drill completed", "Network and WhatsApp providers approved", "Pilot support and rollback plans signed off",
)


def checklist_markdown(title: str, items: Iterable[str]) -> str:
    return f"# {title}\n\n" + "\n".join(f"- [ ] {item}" for item in items) + "\n"


def generate_checklists(directory: str | Path) -> tuple[Path, Path]:
    destination = Path(directory); destination.mkdir(parents=True, exist_ok=True)
    validation = destination / "validation-checklist.md"
    deployment = destination / "deployment-checklist.md"
    validation.write_text(checklist_markdown("Validation Checklist", VALIDATION_CHECKLIST), encoding="utf-8")
    deployment.write_text(checklist_markdown("Deployment Checklist", DEPLOYMENT_CHECKLIST), encoding="utf-8")
    return validation, deployment


def generate_pilot_readiness_report(readiness: Mapping[str, Any], destination: str | Path) -> Path:
    path = Path(destination)
    lines = ["# GutVibe Pilot Readiness Report", f"Generated: {utc_now()}", ""]
    for key in ("module_health", "test_results", "hardware_status", "security_status", "deployment_readiness", "overall_score"):
        lines.append(f"- **{key.replace('_', ' ').title()}**: {readiness[key]}%")
    lines += ["", f"**Ready:** {'Yes' if readiness.get('ready') else 'No'}", "", "## Blockers"]
    lines += [f"- {item}" for item in readiness.get("blockers", [])] or ["- None"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_evaluation_pdf(store: EvaluationStore, run_id: str, destination: str | Path,
                            clinical: Iterable[ClinicalValidation] = ()) -> Path:
    """Create a concise, non-diagnostic PDF evidence report."""
    path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet(); story = [Paragraph("GutVibe Evaluation Report", styles["Title"]),
                                            Paragraph(f"Run {run_id} · {utc_now()}", styles["Normal"]), Spacer(1, 12)]
    data = [["Category", "Check", "Status", "Duration (ms)"]]
    data += [[r["category"], r["check_name"], r["status"].upper(), f'{r["duration_ms"]:.2f}'] for r in store.results(run_id)]
    table = Table(data, repeatRows=1, colWidths=[75, 220, 65, 80])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565c0")),
                               ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .25, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    story.append(table)
    reviews = list(clinical)
    if reviews:
        story += [Spacer(1, 12), Paragraph("Clinical validation support", styles["Heading2"])]
        for review in reviews:
            story.append(Paragraph(f"{review.check_name}: {review.validation_status} — {review.clinician_comments}", styles["Normal"]))
    story += [Spacer(1, 12), Paragraph("Evaluation evidence only; not a medical diagnosis or regulatory approval.", styles["Italic"])]
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return path


def render_pilot_readiness_dashboard(store: EvaluationStore, run_id: str) -> None:
    """Render the requested dashboard when called from a Streamlit admin route."""
    import streamlit as st
    readiness = PilotReadinessCalculator(store).calculate(run_id)
    st.header("Pilot Readiness")
    columns = st.columns(6)
    for column, (label, key) in zip(columns, (("Module Health", "module_health"), ("Test Results", "test_results"),
                                               ("Hardware Status", "hardware_status"), ("Security Status", "security_status"),
                                               ("Deployment", "deployment_readiness"), ("Overall", "overall_score"))):
        column.metric(label, f"{readiness[key]}%")
    st.progress(readiness["overall_score"] / 100)
    (st.success if readiness["ready"] else st.error)("Ready for pilot" if readiness["ready"] else "Not ready: resolve all blockers")
    st.dataframe(store.results(run_id), use_container_width=True)

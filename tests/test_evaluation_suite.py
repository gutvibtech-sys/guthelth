import pytest

from evaluation_suite import (
    AI_CHECKS,
    DEPLOYMENT_CHECKLIST,
    HARDWARE,
    MODULES,
    SECURITY,
    WORKFLOW,
    ClinicalValidation,
    EvaluationStore,
    EvaluationSuite,
    PilotReadinessCalculator,
    ProbeResult,
    Status,
    VALIDATION_CHECKLIST,
    checklist_markdown,
    generate_checklists,
    generate_evaluation_pdf,
    generate_pilot_readiness_report,
)


@pytest.fixture
def store(tmp_path):
    return EvaluationStore(tmp_path / "evaluation.db")


def passing_probes():
    names = MODULES + WORKFLOW + HARDWARE + SECURITY + AI_CHECKS
    return {name: (lambda: ProbeResult(Status.PASS, "validated", {"source": "test"})) for name in names}


def test_store_creates_all_phase_13_tables(store):
    with store.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"evaluation_results", "validation_runs", "pilot_readiness", "performance_metrics"} <= tables


def test_full_run_persists_every_required_check(store):
    run_id, results = EvaluationSuite(store, passing_probes()).run({"build": "test"})
    assert len(results) == len(MODULES + WORKFLOW + HARDWARE + SECURITY + AI_CHECKS)
    assert {result.status for result in results} == {"pass"}
    assert len(store.results(run_id)) == len(results)
    with store.connect() as conn:
        run = conn.execute("SELECT * FROM validation_runs WHERE run_id=?", (run_id,)).fetchone()
    assert run["status"] == "complete"
    assert run["completed_at"]


def test_missing_probe_is_visible_and_blocks_readiness(store):
    run_id, results = EvaluationSuite(store).run()
    assert all(result.status == "skipped" for result in results)
    readiness = PilotReadinessCalculator(store).calculate(run_id)
    assert readiness["overall_score"] == 0
    assert readiness["ready"] is False
    assert "Camera" in readiness["blockers"]


def test_probe_false_and_exception_are_recorded_without_aborting(store):
    suite = EvaluationSuite(store, {"Camera": lambda: False, "Login": lambda: 1 / 0})
    _, results = suite.run()
    indexed = {result.check_name: result for result in results}
    assert indexed["Camera"].status == "fail"
    assert indexed["Login"].status == "error"
    assert "ZeroDivisionError" in indexed["Login"].message


def test_passing_run_is_pilot_ready_and_persisted(store):
    run_id, _ = EvaluationSuite(store, passing_probes()).run()
    readiness = PilotReadinessCalculator(store).calculate(run_id)
    assert readiness["overall_score"] == 100
    assert readiness["ready"] is True
    with store.connect() as conn:
        assert conn.execute("SELECT overall_score FROM pilot_readiness WHERE run_id=?", (run_id,)).fetchone()[0] == 100


def test_benchmark_and_resource_metrics_are_persisted(store):
    suite = EvaluationSuite(store)
    run_id = store.start_run()
    duration = suite.benchmark(run_id, "Face analysis latency", lambda: sum(range(20)), repeats=2, threshold=1000)
    with suite.resource_benchmark(run_id):
        sum(range(100))
    with store.connect() as conn:
        metrics = {row[0]: row[1] for row in conn.execute(
            "SELECT metric_name, unit FROM performance_metrics WHERE run_id=?", (run_id,))}
    assert duration >= 0
    assert metrics == {"Face analysis latency": "ms", "CPU usage": "cpu_ms", "Memory usage": "MiB", "Startup time": "ms"}


def test_required_performance_operations_are_benchmarked(store):
    suite = EvaluationSuite(store)
    run_id = store.start_run()
    values = suite.run_performance_checks(run_id, {
        "Startup time": lambda: None,
        "Report generation time": lambda: b"report",
        "Face analysis latency": lambda: {"face": True},
    }, repeats=1)
    assert set(values) == {"Startup time", "Report generation time", "Face analysis latency"}


def test_benchmark_rejects_invalid_repeat_count(store):
    with pytest.raises(ValueError, match="positive"):
        EvaluationSuite(store).benchmark(store.start_run(), "Report generation time", lambda: None, repeats=0)


def test_reports_and_checklists_are_generated(store, tmp_path):
    run_id, _ = EvaluationSuite(store, passing_probes()).run()
    readiness = PilotReadinessCalculator(store).calculate(run_id)
    pdf = generate_evaluation_pdf(store, run_id, tmp_path / "evaluation.pdf", [ClinicalValidation(
        "Wellness Score", "Dr Example", "Compared with reviewed reference", 82, 80, "validated")])
    pilot = generate_pilot_readiness_report(readiness, tmp_path / "pilot.md")
    assert pdf.read_bytes().startswith(b"%PDF")
    assert "Overall Score" in pilot.read_text(encoding="utf-8")
    assert "- [ ]" in checklist_markdown("Validation Checklist", VALIDATION_CHECKLIST)
    assert "rollback" in checklist_markdown("Deployment Checklist", DEPLOYMENT_CHECKLIST)
    validation, deployment = generate_checklists(tmp_path / "checklists")
    assert validation.exists() and deployment.exists()


def test_database_can_be_reopened_without_data_loss(tmp_path):
    database = tmp_path / "integration.db"
    first = EvaluationStore(database)
    run_id, _ = EvaluationSuite(first, passing_probes()).run()
    second = EvaluationStore(database)
    assert len(second.results(run_id)) == len(MODULES + WORKFLOW + HARDWARE + SECURITY + AI_CHECKS)

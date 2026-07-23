import sqlite3

import pytest

from physiological_engine import (
    BiologicalAge,
    FutureRPPGProvider,
    HeartRate,
    HeartRateVariability,
    PhysiologicalEngine,
    PhysiologicalResult,
    RespiratoryRate,
    SensorSignalProvider,
    SignalQuality,
    StressIndex,
    DISCLAIMER,
)


class ExampleWatchProvider(SensorSignalProvider):
    name = "example-watch"

    def extract_from_readings(self, readings):
        return PhysiologicalResult(
            HeartRate(readings["heart_rate"]),
            RespiratoryRate(readings["respiratory_rate"]),
            HeartRateVariability(readings.get("hrv")),
            SignalQuality(0.91, "good"),
            BiologicalAge(None),
            StressIndex(24),
            0.88,
            {"device_id": "redacted"},
        )


def test_sensor_provider_persists_all_three_relations(tmp_path):
    database = str(tmp_path / "physiology.db")
    engine = PhysiologicalEngine(database)
    engine.register_provider(ExampleWatchProvider())

    measurement_id = engine.analyze_smart_watch(
        "PAT-0001", "example-watch",
        {"heart_rate": 72, "respiratory_rate": 15.5, "hrv": 43},
    )

    history = engine.history("PAT-0001")
    assert measurement_id.startswith("PHY-")
    assert history.iloc[0]["heart_rate_bpm"] == 72
    assert history.iloc[0]["respiratory_rate_bpm"] == 15.5
    assert history.iloc[0]["hrv_rmssd_ms"] == 43
    assert history.iloc[0]["measurement_source"] == "example-watch"
    assert history.iloc[0]["confidence_score"] == 0.88
    assert history.iloc[0]["signal_quality"] == 0.91

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        disclaimer = connection.execute(
            "SELECT disclaimer FROM wellness_scores WHERE measurement_id = ?",
            (measurement_id,),
        ).fetchone()[0]
    assert {"physiological_measurements", "signal_quality", "wellness_scores"} <= tables
    assert disclaimer == DISCLAIMER


def test_quality_and_confidence_are_bounded():
    with pytest.raises(ValueError, match="signal quality"):
        SignalQuality(1.1)
    with pytest.raises(ValueError, match="confidence"):
        PhysiologicalResult(None, None, HeartRateVariability(), SignalQuality(1),
                            BiologicalAge(), StressIndex(), -0.1)


def test_unknown_and_future_providers_fail_explicitly(tmp_path):
    engine = PhysiologicalEngine(str(tmp_path / "physiology.db"))
    with pytest.raises(ValueError, match="Unknown signal provider"):
        engine.analyze_bluetooth("PAT-1", "missing", {})
    with pytest.raises(NotImplementedError, match="validated rPPG"):
        FutureRPPGProvider().extract_from_frames([])


def test_history_is_patient_scoped(tmp_path):
    engine = PhysiologicalEngine(str(tmp_path / "physiology.db"))
    engine.register_provider(ExampleWatchProvider())
    readings = {"heart_rate": 70, "respiratory_rate": 14}
    engine.analyze_bluetooth("PAT-A", "example-watch", readings)
    engine.analyze_medical_sensor("PAT-B", "example-watch", readings)
    assert len(engine.history("PAT-A")) == 1
    assert set(engine.history("PAT-A")["measurement_source"]) == {"example-watch"}

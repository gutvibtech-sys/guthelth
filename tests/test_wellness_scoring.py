import sqlite3

import pytest

from wellness_scoring import DISCLAIMER, WellnessScoringEngine, get_connection


def complete_inputs(sleep=60):
    return {
        "patient": {"circadian_score": "80", "gut_health_score": "70", "sleep_score": str(sleep),
                    "nutrition_score": "75", "activity_score": "50"},
        "face_scan": {"face_count": 1},
        "face_landmarks": {"measurement_id": "landmark"},
        "skin": {"skin_uniformity_score": .8},
        "physiology": {"heart_rate_bpm": 70, "respiratory_rate_bpm": 16,
                       "stress_index": 20, "signal_quality": .9, "confidence_score": .8},
    }


def test_assessment_is_bounded_transparent_and_persisted(tmp_path):
    database = str(tmp_path / "wellness.db")
    engine = WellnessScoringEngine(database)
    result = engine.assess("PAT-1", complete_inputs(), "2026-01-01T00:00:00Z")

    assert 0 <= result.overall_score <= 100
    assert result.data_completeness_score == 100
    assert result.trend_score == 50
    assert result.disclaimer == DISCLAIMER
    assert len(result.components) == 7
    assert all(component.explanation for component in result.components)
    with get_connection(database) as conn:
        assert conn.execute("SELECT count(*) FROM wellness_score").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM wellness_history").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM score_components").fetchone()[0] == 7


def test_missing_data_reduces_completeness_and_renormalizes(tmp_path):
    engine = WellnessScoringEngine(str(tmp_path / "wellness.db"))
    result = engine.assess("PAT-2", {"patient": {"sleep_score": "80"}}, persist=False)

    assert result.overall_score == 80
    assert result.data_completeness_score == 10
    missing = [component for component in result.components if component.score is None]
    assert len(missing) == 6
    assert all(component.explanation.startswith("Not scored") for component in missing)


def test_trend_compares_with_previous_assessment(tmp_path):
    engine = WellnessScoringEngine(str(tmp_path / "wellness.db"))
    engine.assess("PAT-3", complete_inputs(sleep=40), "2026-01-01T00:00:00Z")
    improved = engine.assess("PAT-3", complete_inputs(sleep=90), "2026-01-02T00:00:00Z")
    assert improved.trend_score > 50
    assert len(engine.history("PAT-3")) == 2


@pytest.mark.parametrize("bad_id", ["", "   "])
def test_patient_id_is_required(tmp_path, bad_id):
    with pytest.raises(ValueError, match="patient_id"):
        WellnessScoringEngine(str(tmp_path / "wellness.db")).assess(bad_id, {})


def test_schema_uses_required_table_names(tmp_path):
    database = str(tmp_path / "wellness.db")
    with get_connection(database):
        pass
    with sqlite3.connect(database) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"wellness_score", "wellness_history", "score_components"} <= names

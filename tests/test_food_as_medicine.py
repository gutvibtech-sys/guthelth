import json
import sqlite3
from datetime import datetime, timezone

import pytest

from food_as_medicine import (DISCLAIMER, CATEGORIES, REGIONS, FoodAsMedicineEngine,
                              NutritionContext, get_connection, schedule_whatsapp_guidance)


def test_schema_and_regional_seed_data(tmp_path):
    database = str(tmp_path / "food.db")
    with get_connection(database) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"food_recommendations", "nutrition_profiles", "food_database", "recommendation_history"} <= tables
        assert {row[0] for row in conn.execute("SELECT DISTINCT category FROM food_database")} <= set(CATEGORIES)
        assert "International" in {row[0] for row in conn.execute("SELECT DISTINCT region FROM food_database")}
    assert set(REGIONS) == {"Kerala", "South Indian", "Indian", "International"}


def test_low_scores_create_safe_personalized_guidance_and_history(tmp_path):
    engine = FoodAsMedicineEngine(str(tmp_path / "food.db"))
    result = engine.generate(NutritionContext("PAT-1", 45, 50, 40, 35, 55, {"signal_quality": 20}, "Kerala"))
    assert result.disclaimer == DISCLAIMER
    assert "nutrition-variety-v1" in result.rule_ids
    assert "Sugar-sweetened drinks" in result.foods_to_reduce
    assert result.hydration and result.meal_timing and result.lifestyle_tips
    with get_connection(engine.database_file) as conn:
        assert conn.execute("SELECT count(*) FROM food_recommendations").fetchone()[0] == 1
        assert conn.execute("SELECT action FROM recommendation_history").fetchone()[0] == "created"


def test_rules_are_replaceable_and_profiles_validate_region(tmp_path):
    class CustomRule:
        rule_id = "reviewed-guideline-v2"
        def apply(self, context, result):
            result["include"].append("Reviewed example food")
            return True
    engine = FoodAsMedicineEngine(str(tmp_path / "food.db"), (CustomRule(),))
    engine.save_profile("PAT-2", "South Indian", {"vegetarian": True}, ["peanuts"])
    assert engine.profile("PAT-2")["region"] == "South Indian"
    result = engine.generate(NutritionContext("PAT-2", region="South Indian"), persist=False)
    assert result.rule_ids == ("reviewed-guideline-v2",)
    assert "Reviewed example food" in result.foods_to_include
    with pytest.raises(ValueError): engine.save_profile("PAT-2", "Mars")


def test_clinician_can_disable_and_audit_recommendation(tmp_path):
    engine = FoodAsMedicineEngine(str(tmp_path / "food.db"))
    recommendation = engine.generate(NutritionContext("PAT-3"))
    engine.clinician_review(recommendation.recommendation_id, False, "Patient preference reviewed", "DOC-1")
    with get_connection(engine.database_file) as conn:
        row = conn.execute("SELECT status,clinician_note FROM food_recommendations").fetchone()
        audit = conn.execute("SELECT action,actor,snapshot_json FROM recommendation_history ORDER BY history_id DESC").fetchone()
    assert tuple(row) == ("disabled", "Patient preference reviewed")
    assert audit[0:2] == ("reviewed", "DOC-1")
    assert json.loads(audit[2])["status"] == "disabled"


def test_whatsapp_jobs_cover_required_followups(tmp_path):
    database = str(tmp_path / "food.db")
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE patients(patient_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO patients VALUES('PAT-4')")
    engine = FoodAsMedicineEngine(database)
    guidance = engine.generate(NutritionContext("PAT-4"), persist=False)
    ids = schedule_whatsapp_guidance("PAT-4", guidance, database)
    assert len(ids) == 4
    with sqlite3.connect(database) as conn:
        rows = conn.execute("SELECT followup_type,message FROM whatsapp_followups").fetchall()
    assert {row[0] for row in rows} == {"daily_reminder", "weekly_check", "food_as_medicine"}
    assert all(DISCLAIMER in row[1] for row in rows)

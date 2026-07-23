"""Configurable, non-diagnostic food and lifestyle guidance for GutVibe.

The rules intentionally use broad food groups rather than treating measurements.
They are independent objects so reviewed guidelines or validated models can replace
them without changing persistence, messaging, or dashboard code.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

DISCLAIMER = "This is general wellness and nutrition guidance. It is NOT a medical prescription."
DATABASE_FILE = "gutvibe_patients.db"
CATEGORIES = ("Hydration", "Fruits", "Vegetables", "Whole Grains", "Protein Sources",
              "Healthy Fats", "Fermented Foods", "Fibre-rich Foods", "Gut-friendly Foods")
REGIONS = ("Kerala", "South Indian", "Indian", "International")

SEED_FOODS = (
    ("Water", "Hydration", "Kerala", "include"),
    ("Tender coconut water", "Hydration", "Kerala", "include"),
    ("Papaya", "Fruits", "Kerala", "include"),
    ("Banana", "Fruits", "South Indian", "include"),
    ("Seasonal leafy vegetables", "Vegetables", "Indian", "include"),
    ("Red rice", "Whole Grains", "Kerala", "include"),
    ("Millets", "Whole Grains", "South Indian", "include"),
    ("Dal and pulses", "Protein Sources", "Indian", "include"),
    ("Nuts and seeds", "Healthy Fats", "Indian", "include"),
    ("Unsweetened curd", "Fermented Foods", "Indian", "include"),
    ("Vegetable sambar", "Fibre-rich Foods", "South Indian", "include"),
    ("Idli with vegetable sambar", "Gut-friendly Foods", "South Indian", "include"),
    ("International foods placeholder", "Gut-friendly Foods", "International", "placeholder"),
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _score(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "": return None
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class NutritionContext:
    patient_id: str
    wellness_score: float | None = None
    lifestyle_score: float | None = None
    activity_score: float | None = None
    nutrition_score: float | None = None
    sleep_score: float | None = None
    physiology: Mapping[str, Any] | None = None
    region: str = "Kerala"


@dataclass(frozen=True)
class Guidance:
    recommendation_id: str
    patient_id: str
    created_at: str
    foods_to_include: tuple[str, ...]
    foods_to_reduce: tuple[str, ...]
    hydration: str
    meal_timing: tuple[str, ...]
    lifestyle_tips: tuple[str, ...]
    weekly_suggestions: tuple[str, ...]
    gut_health_tips: tuple[str, ...]
    rule_ids: tuple[str, ...]
    disclaimer: str = DISCLAIMER


class RecommendationRule(Protocol):
    rule_id: str
    def apply(self, context: NutritionContext, result: dict[str, list[str]]) -> bool: ...


class LowScoreRule:
    """Adds category-level guidance below a configurable score threshold."""
    def __init__(self, rule_id: str, field: str, threshold: float, bucket: str, messages: Sequence[str]):
        self.rule_id, self.field, self.threshold, self.bucket, self.messages = rule_id, field, threshold, bucket, tuple(messages)

    def apply(self, context: NutritionContext, result: dict[str, list[str]]) -> bool:
        value = _score(getattr(context, self.field))
        if value is None or value >= self.threshold: return False
        result[self.bucket].extend(self.messages)
        return True


DEFAULT_RULES: tuple[RecommendationRule, ...] = (
    LowScoreRule("nutrition-variety-v1", "nutrition_score", 65, "include", ("Add a variety of seasonal vegetables and fruit", "Choose whole grains and pulses more often")),
    LowScoreRule("activity-fuel-v1", "activity_score", 65, "lifestyle", ("Build movement into the day gradually, according to your abilities",)),
    LowScoreRule("sleep-routine-v1", "sleep_score", 65, "lifestyle", ("Keep a consistent sleep and wake routine", "Avoid large meals close to bedtime")),
    LowScoreRule("lifestyle-regularity-v1", "lifestyle_score", 65, "meal_timing", ("Aim for regular meal times", "Allow time to eat slowly and mindfully")),
    LowScoreRule("wellness-basics-v1", "wellness_score", 65, "reduce", ("Sugar-sweetened drinks", "Frequently fried and highly processed foods")),
)


def get_connection(database_file: str = DATABASE_FILE) -> sqlite3.Connection:
    path = Path(database_file); path.touch(exist_ok=True); path.chmod(0o600)
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS food_database (
        food_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
        region TEXT NOT NULL, guidance_type TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
        is_active INTEGER NOT NULL DEFAULT 1, UNIQUE(name, category, region));
      CREATE TABLE IF NOT EXISTS nutrition_profiles (
        patient_id TEXT PRIMARY KEY, region TEXT NOT NULL, preferences_json TEXT NOT NULL DEFAULT '{}',
        exclusions_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS food_recommendations (
        recommendation_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, created_at TEXT NOT NULL,
        guidance_json TEXT NOT NULL, rule_ids_json TEXT NOT NULL, disclaimer TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active', clinician_note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS recommendation_history (
        history_id INTEGER PRIMARY KEY AUTOINCREMENT, recommendation_id TEXT NOT NULL,
        patient_id TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
        snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS idx_food_recommendations_patient ON food_recommendations(patient_id, created_at DESC);
    """)
    conn.executemany("INSERT OR IGNORE INTO food_database(name,category,region,guidance_type) VALUES(?,?,?,?)", SEED_FOODS)
    conn.commit(); return conn


class FoodAsMedicineEngine:
    def __init__(self, database_file: str = DATABASE_FILE, rules: Sequence[RecommendationRule] = DEFAULT_RULES):
        self.database_file, self.rules = database_file, tuple(rules)
        with get_connection(database_file): pass

    def load_context(self, patient_id: str) -> NutritionContext:
        """Combine registration, wellness scoring, face/skin-derived score, and physiology data."""
        from wellness_scoring import WellnessScoringEngine
        scoring = WellnessScoringEngine(self.database_file)
        inputs = scoring.load_inputs(patient_id)
        latest = scoring.latest(patient_id)
        patient = inputs.get("patient") or {}
        profile = self.profile(patient_id)
        lifestyle_values = [_score(patient.get(k)) for k in ("circadian_score", "gut_health_score")]
        lifestyle_values = [v for v in lifestyle_values if v is not None]
        return NutritionContext(patient_id, latest.overall_score if latest else None,
            sum(lifestyle_values) / len(lifestyle_values) if lifestyle_values else None,
            _score(patient.get("activity_score")), _score(patient.get("nutrition_score")),
            _score(patient.get("sleep_score")), inputs.get("physiology"), profile.get("region", "Kerala"))

    def generate(self, context: NutritionContext, persist: bool = True) -> Guidance:
        if not context.patient_id.strip(): raise ValueError("patient_id is required")
        region = context.region if context.region in REGIONS else "Kerala"
        with get_connection(self.database_file) as conn:
            foods = conn.execute("SELECT name FROM food_database WHERE is_active=1 AND guidance_type='include' AND region IN (?, 'Indian') ORDER BY category", (region,)).fetchall()
        result = {"include": [r[0] for r in foods], "reduce": ["Excess added sugar", "Frequently ultra-processed foods"],
                  "meal_timing": ["Keep meals reasonably regular and finish when comfortably satisfied"],
                  "lifestyle": ["Pair balanced meals with regular movement and adequate rest"]}
        applied = [rule.rule_id for rule in self.rules if rule.apply(context, result)]
        physiology = context.physiology or {}
        quality = _score(physiology.get("signal_quality"))
        hydration = "Drink water regularly through the day; use thirst, climate, and activity as guides. Individual needs vary."
        if quality is not None and quality < 50:
            result["lifestyle"].append("Repeat low-quality wellness measurements before using them to personalize guidance")
        guidance = Guidance(f"FOOD-{uuid.uuid4().hex[:12].upper()}", context.patient_id, _now(),
            tuple(dict.fromkeys(result["include"])), tuple(dict.fromkeys(result["reduce"])), hydration,
            tuple(dict.fromkeys(result["meal_timing"])), tuple(dict.fromkeys(result["lifestyle"])),
            ("Plan several different plant foods across the week", "Prepare one balanced regional recipe at home"),
            ("Increase fibre variety gradually and drink adequate water", "Choose fermented foods only if they suit you"), tuple(applied))
        if persist: self._store(guidance)
        return guidance

    def _store(self, guidance: Guidance) -> None:
        payload = json.dumps(asdict(guidance))
        with get_connection(self.database_file) as conn:
            conn.execute("INSERT INTO food_recommendations VALUES(?,?,?,?,?,?,'active','',?)",
                (guidance.recommendation_id, guidance.patient_id, guidance.created_at, payload, json.dumps(guidance.rule_ids), DISCLAIMER, guidance.created_at))
            conn.execute("INSERT INTO recommendation_history(recommendation_id,patient_id,action,actor,snapshot_json,created_at) VALUES(?,?,?,?,?,?)",
                (guidance.recommendation_id, guidance.patient_id, "created", "engine", payload, guidance.created_at))
            conn.commit()

    def save_profile(self, patient_id: str, region: str = "Kerala", preferences: Mapping[str, Any] | None = None, exclusions: Sequence[str] = ()) -> None:
        if region not in REGIONS: raise ValueError("Unsupported food region")
        with get_connection(self.database_file) as conn:
            conn.execute("INSERT INTO nutrition_profiles VALUES(?,?,?,?,?) ON CONFLICT(patient_id) DO UPDATE SET region=excluded.region,preferences_json=excluded.preferences_json,exclusions_json=excluded.exclusions_json,updated_at=excluded.updated_at",
                (patient_id, region, json.dumps(preferences or {}), json.dumps(list(exclusions)), _now()))
            conn.commit()

    def profile(self, patient_id: str) -> dict[str, Any]:
        with get_connection(self.database_file) as conn:
            row = conn.execute("SELECT * FROM nutrition_profiles WHERE patient_id=?", (patient_id,)).fetchone()
        return dict(row) if row else {"patient_id": patient_id, "region": "Kerala"}

    def clinician_review(self, recommendation_id: str, enabled: bool, note: str, actor: str = "clinician") -> None:
        status, now = ("active" if enabled else "disabled"), _now()
        with get_connection(self.database_file) as conn:
            row = conn.execute("SELECT * FROM food_recommendations WHERE recommendation_id=?", (recommendation_id,)).fetchone()
            if not row: raise LookupError("Recommendation not found")
            conn.execute("UPDATE food_recommendations SET status=?,clinician_note=?,updated_at=? WHERE recommendation_id=?", (status, note, now, recommendation_id))
            snapshot = dict(row); snapshot.update(status=status, clinician_note=note)
            conn.execute("INSERT INTO recommendation_history(recommendation_id,patient_id,action,actor,snapshot_json,created_at) VALUES(?,?,?,?,?,?)",
                (recommendation_id, row["patient_id"], "reviewed", actor, json.dumps(snapshot), now)); conn.commit()


def schedule_whatsapp_guidance(patient_id: str, guidance: Guidance, database_file: str = DATABASE_FILE) -> list[str]:
    """Create consent-gated CRM jobs; dispatch remains the WhatsApp adapter's job."""
    from whatsapp_crm import schedule_followup
    now = datetime.now(timezone.utc)
    return [
        schedule_followup(patient_id, "daily_reminder", f"Food reminder: choose a balanced meal and water today. {DISCLAIMER}", now + timedelta(days=1), "daily", database_file),
        schedule_followup(patient_id, "weekly_check", f"Weekly tip: {guidance.weekly_suggestions[0]}. {DISCLAIMER}", now + timedelta(days=7), "weekly", database_file),
        schedule_followup(patient_id, "food_as_medicine", f"Recipe idea: combine a whole grain, vegetables, and a protein source. {DISCLAIMER}", now + timedelta(days=2), "", database_file),
        schedule_followup(patient_id, "food_as_medicine", f"Nutrition follow-up: review what worked for you this week. {DISCLAIMER}", now + timedelta(days=7), "", database_file),
    ]


def render_nutrition_dashboard(patient_id: str) -> None:
    import streamlit as st
    engine = FoodAsMedicineEngine()
    guidance = engine.generate(engine.load_context(patient_id))
    st.error(DISCLAIMER)
    st.subheader("Daily Wellness Nutrition Plan")
    c1, c2 = st.columns(2); c1.markdown("**Foods to include**\n- " + "\n- ".join(guidance.foods_to_include)); c2.markdown("**Foods to reduce**\n- " + "\n- ".join(guidance.foods_to_reduce))
    st.subheader("Hydration Tracker"); st.info(guidance.hydration); st.progress(0, text="Log water through your connected tracker (integration placeholder)")
    tabs = st.tabs(["Meal Planner", "Weekly Suggestions", "Gut Health Tips"])
    tabs[0].write(" • ".join(guidance.meal_timing)); tabs[1].write(guidance.weekly_suggestions); tabs[2].write(guidance.gut_health_tips)


def render_clinician_recommendations() -> None:
    import streamlit as st
    with get_connection() as conn:
        rows = conn.execute("SELECT recommendation_id,patient_id,status,clinician_note,created_at FROM food_recommendations ORDER BY created_at DESC").fetchall()
    st.subheader("Food & nutrition recommendation review")
    if not rows: st.info("No nutrition recommendations are available for review."); return
    selected = st.selectbox("Recommendation", [r["recommendation_id"] for r in rows], key="food_review_id")
    row = next(r for r in rows if r["recommendation_id"] == selected)
    enabled = st.checkbox("Enabled", row["status"] == "active", key="food_review_enabled")
    note = st.text_area("Clinician edit / review note", row["clinician_note"], key="food_review_note")
    if st.button("Save nutrition review", key="food_review_save"):
        FoodAsMedicineEngine().clinician_review(selected, enabled, note); st.success("Review saved with audit history.")

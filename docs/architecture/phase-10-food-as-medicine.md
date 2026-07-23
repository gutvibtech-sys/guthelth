# Phase 10 — Food as Medicine Recommendation Engine

## Safety boundary

> **This is general wellness and nutrition guidance. It is NOT a medical prescription.**

The engine provides food-group and routine suggestions only. It does not diagnose disease, prescribe treatment or medicine, infer nutritional deficiencies, or replace a clinician. Physiological, face, and skin observations are consumed only through the existing non-diagnostic wellness assessment; low-quality measurements trigger a suggestion to repeat measurement rather than a food intervention.

## Modular flow

```text
Registration ─┐
Face / Skin ──┼─> WellnessScoringEngine ─> NutritionContext
Physiology ───┘                              │
Nutrition profile / regional foods ──────────┼─> RecommendationRule[]
                                             │
                       Guidance ─> SQLite history / Nutrition dashboard
                                ├> consent-gated WhatsApp schedules
                                └> clinician review, note, enable/disable
```

`RecommendationRule` is a small protocol. The initial threshold rules can be individually replaced with versioned, reviewed guidelines or a clinically validated model while preserving the context, output, audit, UI, and messaging contracts. Every generated record stores applied rule IDs and the safety disclaimer.

## Data model

| Table | Purpose |
|---|---|
| `food_database` | Categorized regional foods, activation state, and extensible metadata |
| `nutrition_profiles` | Patient region, preferences, and exclusions |
| `food_recommendations` | Versioned generated guidance and clinician-controlled status |
| `recommendation_history` | Append-only creation and clinician-review audit events |

The initial database covers Kerala, South Indian, and pan-Indian examples. International foods are an explicit placeholder. Categories include hydration, fruits, vegetables, whole grains, protein, healthy fats, fermented foods, fibre-rich foods, and gut-friendly foods.

## Integrations and interfaces

- `load_context()` joins registration, unified wellness scoring, underlying face/skin inputs, and the latest physiological signal.
- The patient dashboard presents a daily plan, hydration tracker placeholder, meal timing, weekly suggestions, and gut-health tips.
- WhatsApp scheduling creates daily reminders, weekly tips, a recipe suggestion, and follow-up guidance. Existing opt-in enforcement remains at dispatch time.
- The doctor dashboard exposes review notes and enable/disable controls. Each action is audited; disabling prevents a recommendation from being treated as active.

## Validation path

Before clinical use, a governance group should approve regional food data, rule thresholds, contraindication handling, accessibility, translations, evidence grading, model validation, and clinician authorization. Future rules should have stable version IDs and tests demonstrating both activation and non-activation boundaries.

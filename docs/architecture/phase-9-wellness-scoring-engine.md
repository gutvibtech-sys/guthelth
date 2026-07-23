# Phase 9: AI Wellness Scoring Engine

## Safety boundary

> **This is a Wellness Assessment and is NOT a Medical Diagnosis.**

The engine summarizes available wellness observations. It does not diagnose or
predict disease, recommend treatment, replace physician judgement, or turn a
missing measurement into an assumed result. Suggestions are general wellness
guidance only.

## Design

`WellnessScoringEngine` is separate from Streamlit and composes seven
`ComponentRule` adapters: face, skin, physiological, lifestyle, nutrition,
activity, and sleep. Nutrition and activity are explicit placeholders; sleep
uses the existing registered placeholder value. Each adapter returns its score,
confidence, configured weight, source, plain-language calculation, and optional
general improvement area. A future validated model can replace one adapter
without changing persistence or the dashboard.

Available component scores are combined by a weighted mean and weights are
renormalized when inputs are absent. This avoids treating missing information as
poor wellness. Data completeness is the percentage of configured weight backed
by usable inputs. Confidence is the weighted mean of source/rule confidence for
available components. Trend is 50 (neutral) on the first assessment, then
`clamp(50 + score_change * 2.5, 0, 100)`.

## Data flow and integrations

The input loader reads patient registration fields, the latest valid face scan,
face landmark measurement, skin measurement, and physiological measurement.
The scoring screen lets the operator calculate a snapshot and presents overall,
confidence, completeness, trend, component explanations, history, and general
suggestions. Stored summaries can be included in consented WhatsApp report
messages and are available to referral/report integrations through
`assessment_summary`.

## Persistence

- `wellness_score`: immutable assessment header and summary metrics.
- `wellness_history`: patient-scoped time series used by the trend chart.
- `score_components`: score, confidence, source, weight, contribution,
  explanation, and improvement area for every component, including missing ones.

SQLite is suitable for this prototype. Production deployments still require
authentication, authorization, encryption/key management, audit logs, retention
controls, validation governance, and clinical/legal review.

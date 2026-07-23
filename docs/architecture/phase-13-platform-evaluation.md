# Phase 13 — Platform Evaluation and Validation Suite

## Purpose and safety boundary

The evaluation layer observes and validates GutVibe without changing existing business
logic. It does not import `main.py`, create patient records, send WhatsApp messages, make
referrals, or activate a device automatically. Deployments inject small callable probes and
remain responsible for using synthetic data, provider sandboxes, and hardware test modes.

## Architecture

```text
deployment/test harness
        │ injected Probe callables
        ▼
EvaluationSuite ──► EvaluationStore (SQLite evidence)
        │                    │
        │                    ├─ evaluation_results
        │                    ├─ validation_runs
        │                    ├─ performance_metrics
        │                    └─ pilot_readiness
        ▼
PDF / checklists / pilot report / Streamlit dashboard
```

`Probe` is a structural protocol. A probe returns a boolean, a `ProbeResult`, or a mapping.
This keeps rules vendor-neutral and allows camera, CRM, security, and model implementations
to be replaced independently. Exceptions are captured as `error` evidence; they do not abort
the rest of a run. Missing probes become `skipped` and block readiness.

## Coverage

| Domain | Checks |
|---|---|
| Modules | Registration, face scan/landmarks, skin, physiology, scoring, nutrition, voice, CRM, referral, hardware, security |
| Workflow | Complete journey, report, WhatsApp delivery, referral, hardware communication |
| Hardware | Camera, height, weight, printer, QR, speaker, microphone, network |
| Security | Login, RBAC, encryption, audit, consent, backup/restore |
| AI | Score, confidence, missing data, signal quality, recommendations |
| Performance | Startup, report generation, face analysis, memory, CPU |

Performance operations are injected too. `benchmark` records median wall latency over
repeat executions, while `resource_benchmark` records elapsed wall time, Python peak traced
memory, and process CPU time. Deployment load tests should supplement these process-level
measurements with operating-system and concurrent-user telemetry.

## Clinical validation support

`ClinicalValidation` records the expert reviewer, clinician comments, predicted and ground
truth values, review timestamp, and validation status. It deliberately does not determine
clinical truth or approve a model. Identifiable source material should remain in an approved
clinical system; report evidence should use de-identified references.

## Readiness policy

Pass earns full credit, warning half credit, and fail/error/skipped no credit. The weighted
score is module health 25%, all test results 20%, hardware 20%, security 20%, and deployment
15%. A pilot is ready only at 85 or above **and** with no failed, errored, or skipped checks.
The conservative blocker rule prevents a high aggregate score from hiding a missing critical
control. Organizations may wrap or replace the calculator with a formally approved policy.

## Evidence and operational runbook

1. Configure production-equivalent probes with synthetic records and test destinations.
2. Record build, kiosk, operator, and environment identifiers in run metadata.
3. Execute checks and named performance benchmarks.
4. Review errors, failures, skipped probes, signal quality, and model confidence.
5. Obtain independent clinician ground-truth review where applicable.
6. Generate the PDF, pilot report, validation checklist, and deployment checklist.
7. Require security and clinical sign-off; retain evidence under the approved policy.
8. Re-run after every build, configuration, hardware, model, or provider change.

The SQLite file is owner-readable/writable where supported, but file permissions are not a
substitute for encrypted production storage, centralized access control, signed artifacts,
immutable audit retention, backup verification, or jurisdiction-specific review.

# Patient Health Report System

A Streamlit application for registering patients, recording patient health metrics, generating PDF health reports, creating QR-code summaries, searching patient records, and reviewing simple population analytics. Phase 12 also supplies a modular security and compliance control plane for production integration.

> **Important:** The current Streamlit routes remain a prototype and are not automatically protected by the Phase 12 library. Do not use real patient data until every route is integrated with authentication/authorization, encrypted fields, audit events, production KMS, TLS, monitored backup/retention jobs, and deployment-specific legal and security review.

## Features

- Register patients with an auto-generated GutVibe Patient ID, demographics, contact details, address, GPS coordinates, height, weight, and auto-calculated BMI.
- Add patient demographic, body metric, lab result, and wellness-score data.
- Generate branded PDF reports with patient details, biomarker summaries, wellness metrics, and a QR-code summary.
- Search existing records by patient name or patient ID.
- Capture a patient face image, validate that exactly one face is present, and store it without AI analysis.
- View population-level summary statistics.
- Download all patient data as CSV.
- Generate individual reports or a ZIP archive of all reports.
- Review provider-neutral physiological wellness estimates and historical trends.
- Calculate a transparent, modular unified wellness score with confidence,
  completeness, history, component explanations, and general wellness suggestions.

## Project structure

```text
guthelth/
├── main.py                         # Streamlit application entrypoint and current app logic
├── physiological_engine.py         # Extensible physiological signal providers and storage
├── wellness_scoring.py             # Transparent unified wellness scoring engine
├── hardware_manager.py             # Vendor-neutral kiosk hardware facade and storage
├── security_compliance.py          # Identity, RBAC, consent, crypto, audit and operations controls
├── requirements.txt                # Python runtime dependencies
├── README.md                       # Project documentation
├── .gitignore                      # Local/runtime file exclusions
├── docs/
│   └── legal/
│       ├── privacy-policy.pdf      # Privacy policy document
│       └── terms-and-conditions.pdf# Terms and conditions document
└── .idea/                          # IDE metadata currently tracked by the repository
```

The application currently uses the following runtime paths:

- `gutvibe_patients.db` — local SQLite database used for patient registration and records.
- `patients_data.csv` — legacy local CSV data store imported automatically when present.
- `patient_reports/` — local folder created by the app for generated PDF reports.
- `face_scans/` — local folder created by the app for captured face images.

Both paths can contain sensitive patient information and are intentionally ignored by Git.

## Requirements

- Python 3.10 or newer is recommended.
- pip or another Python package manager.

Python dependencies are listed in [`requirements.txt`](requirements.txt).

## Quick start

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the Streamlit app:

   ```bash
   streamlit run main.py
   ```

4. Open the local URL printed by Streamlit in your browser.

## Data and privacy notes

This application handles personally identifiable information and health-related data. Before using it with real patient data, implement and review controls for:

- User authentication and role-based authorization.
- Encryption at rest and in transit.
- Production-grade database encryption/key management and managed backups beyond local SQLite file permissions.
- Audit logging for record creation, viewing, updates, and exports.
- Data retention and deletion policies.
- Secure backups and recovery procedures.
- Legal/compliance review for your jurisdiction and use case.

## Generated files

The following files and directories are local runtime artifacts and should not be committed:

- `gutvibe_patients.db`
- `gutvibe_patients.db-shm`
- `gutvibe_patients.db-wal`
- `patients_data.csv`
- `patient_reports/`
- `face_scans/`
- `__pycache__/`
- virtual environments such as `.venv/`
- local Streamlit secrets such as `.streamlit/secrets.toml`

## Development notes

The current codebase is intentionally small and keeps the full Streamlit application in `main.py`. Future phases should split storage, report generation, QR generation, clinical interpretation rules, and UI pages into separate modules while preserving existing behavior.

Recommended future checks:

```bash
python -m py_compile main.py
streamlit run main.py
```

## Architecture documentation

- [Phase 4: GutVibe AI Wellness Kiosk Architecture](docs/architecture/phase-4-ai-wellness-kiosk.md) — production-ready software architecture covering multilingual AI assistant flow, consent, registration, measurements, future modules, reporting, admin dashboards, UML diagrams, database schema, API structure, and screen flow.
- [Phase 6: AI Voice Assistant for GutVibe Wellness Kiosk](docs/architecture/phase-6-ai-voice-assistant.md) — standalone voice and touch assistant interfaces with Malayalam, English, Tamil, and Hindi prompts, automatic language detection hooks, and pluggable STT/TTS/conversation providers.
- [Phase 8: AI Physiological Signal Engine](docs/architecture/phase-8-physiological-signal-engine.md) — modular camera, Bluetooth, smart-watch, and sensor interfaces, persistence schema, dashboard, and safety boundaries.
- [Phase 9: AI Wellness Scoring Engine](docs/architecture/phase-9-wellness-scoring-engine.md) — modular component rules, unified score, confidence, completeness, trend, transparent explanations, persistence, and safety boundaries.
- [Phase 10: Food as Medicine Recommendation Engine](docs/architecture/phase-10-food-as-medicine.md) — regional food data, replaceable recommendation rules, nutrition planning, WhatsApp follow-ups, and audited clinician review.
- [Phase 11: AI Wellness Kiosk Hardware Integration](docs/architecture/phase-11-hardware-integration.md) — replaceable device contracts, SQLite health/audit storage, diagnostics, calibration, and cross-platform adapter guidance.
- [Phase 12: Security, Privacy, Consent and Compliance](docs/architecture/phase-12-security-compliance.md) — authentication/MFA hooks, deny-by-default RBAC, versioned consent, KMS-backed field encryption, hash-chained audit, retention, device registration, verified backup/restore, dashboard metrics and an India DPDP readiness map.

## Phase 12: security and compliance framework

`security_compliance.py` is a UI-independent control plane with all six required
roles, scrypt password storage, login lockout, MFA provider hooks, expiring
sessions, consent withdrawal/re-consent history, authenticated field encryption,
tamper-evident audit events, device registration, retention candidates, verified
SQLite backup/restore and security dashboard metrics. `KeyProvider` and injected
MFA verification keep cloud KMS/HSM and regional identity choices replaceable.

For local development, generate an encryption key without committing it:

```bash
export GUTVIBE_DATA_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

Read the Phase 12 architecture and runbook before integration. It explicitly
documents what remains deployment work and provides a modular path for DPDP,
HIPAA, GDPR and future regional policy packages without redesigning the core.

## Phase 11: Kiosk hardware integration

`hardware_manager.py` is the application-facing hardware boundary. Camera,
height, weight, printer, QR, speaker, microphone, payment, and network vendors
implement structural provider protocols and are registered at kiosk startup.
Patient registration, face scan, wellness scoring, voice, WhatsApp, and nutrition
features consume normalized manager results rather than vendor SDKs. The
**Hardware** admin page shows inventory, required device health (including
battery/UPS), last checks, calibration history, restart/diagnostic controls, and
error logs. Payments are an interface-only placeholder and are deliberately
blocked until a later, security-reviewed UPI phase.

## Legal documents

Legal documents are stored under `docs/legal/`:

- [`privacy-policy.pdf`](docs/legal/privacy-policy.pdf)
- [`terms-and-conditions.pdf`](docs/legal/terms-and-conditions.pdf)

## Phase 5: Doctor Referral & Telemedicine Integration

The application now includes a modular doctor referral workflow in `doctor_referral.py`:

- A **Consult a Doctor** follow-up action appears after a wellness report is generated or opened from patient search.
- Patients can select a hospital, specialty, and available doctor.
- A secure payment integration placeholder records consultation fee authorization without processing real payments.
- Consultation requests capture patient ID, wellness report fields, face scan summary, height, weight, BMI, latest skin analysis summary, and consent status.
- The **Doctor Dashboard** supports patient report review, consultation notes, recommended laboratory tests, and hospital referral notes.
- The **Hospital Admin Dashboard** supports doctor directory management, appointment review, and consultation fee management.
- Telemedicine routing is isolated behind placeholder metadata so eSanjeevani or other providers can be integrated without changing existing patient modules.


## Phase 6: AI Voice Assistant for GutVibe Wellness Kiosk

The new `voice_assistant.py` module adds a standalone, provider-agnostic conversation layer for kiosk deployments without modifying existing patient, analysis, report, or referral modules:

- Automatically welcomes users when a presence event indicates they have approached the kiosk.
- Supports Malayalam, English, Tamil, and Hindi prompts with automatic spoken-language detection hooks.
- Provides voice and touch interaction paths through the same kiosk journey.
- Guides users through consent, registration, face scan, height and weight measurement, wellness report preparation, doctor referral, and completion.
- Defines modular Speech-to-Text, Text-to-Speech, and conversation provider interfaces so future AI vendors can be plugged in later.
- Keeps conversation state separate from medical analysis data and stores only session/workflow metadata.

## Phase 7: WhatsApp CRM & Wellness Follow-up

The provider-neutral `whatsapp_crm.py` module adds consent-controlled WhatsApp engagement:

- Sends a wellness summary, PDF report, and QR download link when a kiosk assessment finishes.
- Schedules daily, weekly, Food as Medicine, and appointment follow-ups, including recurring dispatch.
- Continues kiosk conversations with safe wellness guidance in Malayalam, English, Tamil, and Hindi.
- Notifies configured doctors of referrals and lets doctors send patient follow-up messages.
- Provides an admin CRM dashboard for communication history, campaign drafts, follow-up schedules, and delivery analytics.
- Uses a `MessagingProvider` protocol; the default sandbox adapter performs no network calls and can be replaced by Meta, Twilio, or another approved provider.

Set `GUTVIBE_PUBLIC_URL` to the public report service base URL. Doctor notification destinations can be configured with `GUTVIBE_DOCTOR_PHONE_<DOCTOR_ID>` (for example, `GUTVIBE_DOCTOR_PHONE_DOC-GV-001`). Production deployments must replace the sandbox provider, use approved WhatsApp templates where required, authenticate webhook events, and retain auditable consent.

## Phase 8: AI Physiological Signal Engine

`physiological_engine.py` adds data models for heart rate, respiratory rate,
HRV, signal quality, biological age, and stress index. The last three wellness
outputs are explicitly non-diagnostic placeholders. `SignalProvider` adapters
keep extraction independent from SQLite persistence and the Streamlit UI;
specialized interfaces are ready for future camera rPPG, Bluetooth devices,
smart watches, and medical sensors. No camera algorithm is claimed or simulated.

The Physiological Dashboard displays latest heart and respiratory rates, signal
quality, wellness trends, and patient-scoped history. Latest estimates can also
flow into wellness reports, consented doctor-referral payloads, and WhatsApp
assessment summaries. All such output carries **“Wellness Estimate Only – Not a
Medical Diagnosis.”**

## Phase 9: AI Wellness Scoring Engine

`wellness_scoring.py` combines available face, landmark, skin, physiological,
lifestyle, nutrition, activity, and sleep observations. Missing components are
shown rather than silently scored, and available weights are renormalized. The
dashboard explains every rule and shows the overall score, confidence, data
completeness, historical trend, component scores, and general improvement areas.
All rules implement small replaceable adapters so future validated models can be
introduced independently.

> **This is a Wellness Assessment and is NOT a Medical Diagnosis.** The score
> never diagnoses or predicts disease and never replaces physician judgement.

## Phase 10: Food as Medicine Recommendation Engine

`food_as_medicine.py` turns available wellness, lifestyle, activity, nutrition,
sleep, and physiological observations into broad food and routine guidance. It
includes Kerala, South Indian, Indian, and international-placeholder food data;
all nine requested recommendation categories; daily and weekly planning; a
hydration tracker placeholder; consent-gated WhatsApp schedules; and clinician
review, edit-note, and disable controls with audit history. Rules implement a
small replaceable protocol so reviewed guidance or validated models can be added
without redesigning storage and delivery.

> **This is general wellness and nutrition guidance. It is NOT a medical prescription.**

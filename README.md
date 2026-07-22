# Patient Health Report System

A Streamlit application for registering patients, recording patient health metrics, generating PDF health reports, creating QR-code summaries, searching patient records, and reviewing simple population analytics.

> **Important:** This project stores patient information in a local SQLite database with owner-only file permissions where supported. Treat it as a prototype unless you have added appropriate authentication, authorization, encryption, audit logging, retention controls, and compliance review for your deployment environment.

## Features

- Register patients with an auto-generated GutVibe Patient ID, demographics, contact details, address, GPS coordinates, height, weight, and auto-calculated BMI.
- Add patient demographic, body metric, lab result, and wellness-score data.
- Generate branded PDF reports with patient details, biomarker summaries, wellness metrics, and a QR-code summary.
- Search existing records by patient name or patient ID.
- View population-level summary statistics.
- Download all patient data as CSV.
- Generate individual reports or a ZIP archive of all reports.

## Project structure

```text
guthelth/
├── main.py                         # Streamlit application entrypoint and current app logic
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

## Legal documents

Legal documents are stored under `docs/legal/`:

- [`privacy-policy.pdf`](docs/legal/privacy-policy.pdf)
- [`terms-and-conditions.pdf`](docs/legal/terms-and-conditions.pdf)

"""Doctor referral and telemedicine workflow for GutVibe.

This module keeps consultation requests, doctor review notes, hospital admin
settings, and future telemedicine provider hand-off metadata separate from the
existing patient, face-scan, and skin-analysis modules.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

from skin_color_analysis import load_latest_skin_color_measurements
from physiological_engine import wellness_summary as physiological_wellness_summary

DATABASE_FILE = "gutvibe_patients.db"

DEFAULT_HOSPITALS = [
    {"hospital_id": "HOSP-GV-001", "name": "GutVibe Partner Hospital", "city": "Bengaluru"},
    {"hospital_id": "HOSP-GV-002", "name": "Integrated Wellness Clinic", "city": "Mumbai"},
    {"hospital_id": "HOSP-GV-003", "name": "Digital Care Telemedicine Hub", "city": "Delhi"},
]

DEFAULT_DOCTORS = [
    {"doctor_id": "DOC-GV-001", "hospital_id": "HOSP-GV-001", "name": "Dr. Asha Menon", "specialty": "Gastroenterology", "availability": "Mon/Wed/Fri 10:00-14:00", "fee": 1200},
    {"doctor_id": "DOC-GV-002", "hospital_id": "HOSP-GV-001", "name": "Dr. Vikram Shah", "specialty": "Nutrition", "availability": "Tue/Thu 11:00-16:00", "fee": 900},
    {"doctor_id": "DOC-GV-003", "hospital_id": "HOSP-GV-002", "name": "Dr. Meera Iyer", "specialty": "Dermatology", "availability": "Mon-Fri 09:00-12:00", "fee": 1100},
    {"doctor_id": "DOC-GV-004", "hospital_id": "HOSP-GV-003", "name": "Dr. Rohan Das", "specialty": "General Medicine", "availability": "Daily 15:00-19:00", "fee": 700},
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def secure_database_file(database_file: str = DATABASE_FILE) -> None:
    if os.path.exists(database_file):
        os.chmod(database_file, 0o600)
        return
    open(database_file, "a", encoding="utf-8").close()
    os.chmod(database_file, 0o600)


def get_connection(database_file: str = DATABASE_FILE) -> sqlite3.Connection:
    secure_database_file(database_file)
    conn = sqlite3.connect(database_file)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hospitals (
            hospital_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            city TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            doctor_id TEXT PRIMARY KEY,
            hospital_id TEXT NOT NULL,
            name TEXT NOT NULL,
            specialty TEXT NOT NULL,
            availability TEXT NOT NULL,
            fee REAL NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(hospital_id) REFERENCES hospitals(hospital_id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS consultation_requests (
            request_id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL,
            hospital_id TEXT NOT NULL,
            doctor_id TEXT NOT NULL,
            specialty TEXT NOT NULL,
            consultation_fee REAL NOT NULL,
            payment_status TEXT NOT NULL,
            consent_status TEXT NOT NULL,
            request_status TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            telemedicine_provider TEXT NOT NULL DEFAULT 'placeholder',
            request_payload_json TEXT NOT NULL,
            doctor_notes TEXT NOT NULL DEFAULT '',
            recommended_lab_tests TEXT NOT NULL DEFAULT '',
            hospital_referral TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE,
            FOREIGN KEY(hospital_id) REFERENCES hospitals(hospital_id),
            FOREIGN KEY(doctor_id) REFERENCES doctors(doctor_id)
        )
    """)
    conn.commit()
    seed_reference_data(conn)
    return conn


def seed_reference_data(conn: sqlite3.Connection) -> None:
    for hospital in DEFAULT_HOSPITALS:
        conn.execute(
            "INSERT OR IGNORE INTO hospitals (hospital_id, name, city) VALUES (?, ?, ?)",
            (hospital["hospital_id"], hospital["name"], hospital["city"]),
        )
    for doctor in DEFAULT_DOCTORS:
        conn.execute(
            """
            INSERT OR IGNORE INTO doctors (doctor_id, hospital_id, name, specialty, availability, fee)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (doctor["doctor_id"], doctor["hospital_id"], doctor["name"], doctor["specialty"], doctor["availability"], doctor["fee"]),
        )
    conn.commit()


def load_hospitals() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query("SELECT * FROM hospitals WHERE is_active = 1 ORDER BY name", conn)


def load_doctors(hospital_id: str | None = None, specialty: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM doctors WHERE is_active = 1"
    params: list[Any] = []
    if hospital_id:
        query += " AND hospital_id = ?"
        params.append(hospital_id)
    if specialty:
        query += " AND specialty = ?"
        params.append(specialty)
    query += " ORDER BY specialty, name"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def _latest_face_summary(patient_id: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT scan_id, captured_at, face_count FROM face_scans WHERE patient_id = ? ORDER BY captured_at DESC LIMIT 1",
            (patient_id,),
        ).fetchone()
    if not row:
        return "No face scan is available."
    return f"Latest scan {row[0]} captured at {row[1]} with {row[2]} detected face."


def _skin_summary(patient_id: str) -> str:
    result = load_latest_skin_color_measurements(patient_id)
    if not result:
        return "No skin analysis is available."
    measurements = result["measurements"]
    keys = ["skin_uniformity_score", "facial_brightness", "facial_redness_index", "analysis_confidence"]
    return "; ".join(f"{key}: {measurements.get(key, '—')}" for key in keys)


def build_consultation_payload(patient: dict[str, Any], consent_status: str) -> dict[str, Any]:
    from wellness_scoring import assessment_summary
    wellness_report = {key: patient.get(key, "") for key in ["hba1c", "cholesterol", "ldl", "hdl", "triglycerides", "vitamin_d", "vitamin_b12", "gut_health_score", "icmr_risk_score", "hrv", "sleep_score", "circadian_score"]}
    return {
        "patient_id": patient.get("patient_id", ""),
        "wellness_report": wellness_report,
        "face_analysis_summary": _latest_face_summary(patient.get("patient_id", "")),
        "height": patient.get("height", ""),
        "weight": patient.get("weight", ""),
        "bmi": patient.get("bmi", ""),
        "skin_analysis_summary": _skin_summary(patient.get("patient_id", "")),
        "physiological_wellness_estimate": physiological_wellness_summary(patient.get("patient_id", "")),
        "unified_wellness_assessment": assessment_summary(patient.get("patient_id", "")),
        "consent_status": consent_status,
    }


def create_consultation_request(patient: dict[str, Any], hospital: dict[str, Any], doctor: dict[str, Any], consent_status: str) -> str:
    request_id = f"CONS-{uuid.uuid4().hex[:10].upper()}"
    payload = build_consultation_payload(patient, consent_status)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO consultation_requests (
                request_id, patient_id, hospital_id, doctor_id, specialty, consultation_fee,
                payment_status, consent_status, request_status, requested_at, telemedicine_provider,
                request_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (request_id, patient["patient_id"], hospital["hospital_id"], doctor["doctor_id"], doctor["specialty"], doctor["fee"], "payment_placeholder_authorized", consent_status, "requested", _utc_now(), "modular_placeholder", json.dumps(payload, sort_keys=True)),
        )
        conn.commit()
    doctor_phone = os.getenv(f"GUTVIBE_DOCTOR_PHONE_{doctor['doctor_id']}", "")
    if doctor_phone:
        from whatsapp_crm import SandboxMessagingProvider, notify_doctor_referral
        notify_doctor_referral(patient["patient_id"], doctor_phone, request_id, SandboxMessagingProvider())
    return request_id


def load_consultations() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(
            """
            SELECT cr.*, p.name AS patient_name, h.name AS hospital_name, d.name AS doctor_name
            FROM consultation_requests cr
            JOIN patients p ON p.patient_id = cr.patient_id
            JOIN hospitals h ON h.hospital_id = cr.hospital_id
            JOIN doctors d ON d.doctor_id = cr.doctor_id
            ORDER BY cr.requested_at DESC
            """,
            conn,
        )


def render_consult_doctor_button(patient: dict[str, Any]) -> None:
    with st.expander("🩺 Consult a Doctor", expanded=False):
        st.caption("Secure payment and telemedicine calls are placeholders so eSanjeevani or another provider can be connected later.")
        hospitals = load_hospitals()
        hospital_label = st.selectbox("Select hospital", hospitals.apply(lambda r: f"{r['name']} — {r['city']}", axis=1).tolist(), key=f"consult_hospital_{patient['patient_id']}")
        hospital = hospitals.iloc[hospitals.apply(lambda r: f"{r['name']} — {r['city']}", axis=1).tolist().index(hospital_label)].to_dict()
        specialties = load_doctors(hospital["hospital_id"])["specialty"].drop_duplicates().tolist()
        specialty = st.selectbox("Select specialty", specialties, key=f"consult_specialty_{patient['patient_id']}")
        doctors = load_doctors(hospital["hospital_id"], specialty)
        doctor_label = st.selectbox("Select available doctor", doctors.apply(lambda r: f"{r['name']} — {r['availability']} — ₹{r['fee']:.0f}", axis=1).tolist(), key=f"consult_doctor_{patient['patient_id']}")
        doctor = doctors.iloc[doctors.apply(lambda r: f"{r['name']} — {r['availability']} — ₹{r['fee']:.0f}", axis=1).tolist().index(doctor_label)].to_dict()
        consent = st.checkbox("Patient consents to share wellness, face, BMI, and skin summaries with the selected doctor.", key=f"consult_consent_{patient['patient_id']}")
        st.info(f"Payment placeholder: consultation fee ₹{doctor['fee']:.0f} will be sent to the configured secure payment gateway in production.")
        if st.button("🔐 Pay Consultation Fee & Request Consultation", key=f"consult_submit_{patient['patient_id']}", use_container_width=True, disabled=not consent):
            request_id = create_consultation_request(patient, hospital, doctor, "consented")
            st.success(f"Consultation request created: {request_id}")


def render_doctor_dashboard() -> None:
    st.markdown("<div class='main-header'><h1>🩺 Doctor Dashboard</h1><p>Review patient reports, add notes, recommend tests, and refer to hospital care.</p></div>", unsafe_allow_html=True)
    consultations = load_consultations()
    if consultations.empty:
        st.info("No consultation requests yet.")
        return
    st.dataframe(consultations[["request_id", "patient_id", "patient_name", "doctor_name", "specialty", "request_status", "requested_at"]], use_container_width=True, hide_index=True)
    selected = st.selectbox("Select consultation", consultations["request_id"].tolist())
    row = consultations[consultations["request_id"] == selected].iloc[0]
    st.json(json.loads(row["request_payload_json"]))
    with st.form(f"doctor_review_{selected}"):
        notes = st.text_area("Consultation notes", value=row.get("doctor_notes", ""), height=120)
        tests = st.text_area("Recommended laboratory tests", value=row.get("recommended_lab_tests", ""), height=90)
        referral = st.text_area("Hospital referral if required", value=row.get("hospital_referral", ""), height=90)
        status = st.selectbox("Status", ["requested", "in_review", "completed", "referred"], index=["requested", "in_review", "completed", "referred"].index(row.get("request_status", "requested")))
        if st.form_submit_button("💾 Save Doctor Review", use_container_width=True):
            with get_connection() as conn:
                conn.execute("UPDATE consultation_requests SET doctor_notes = ?, recommended_lab_tests = ?, hospital_referral = ?, request_status = ? WHERE request_id = ?", (notes, tests, referral, status, selected))
                conn.commit()
            st.success("Doctor review saved.")
    with st.expander("💬 Send WhatsApp follow-up to patient"):
        doctor_message = st.text_area("Follow-up message", key=f"doctor_whatsapp_{selected}")
        if st.button("Send doctor follow-up", key=f"send_doctor_whatsapp_{selected}", disabled=not doctor_message.strip()):
            from whatsapp_crm import SandboxMessagingProvider, send_patient_message
            try:
                send_patient_message(row["patient_id"], doctor_message, "doctor_followup", SandboxMessagingProvider(), metadata={"request_id": selected, "doctor_id": row["doctor_id"]})
                st.success("Doctor follow-up sent and added to communication history.")
            except PermissionError as exc:
                st.warning(str(exc))


def render_hospital_admin_dashboard() -> None:
    st.markdown("<div class='main-header'><h1>🏥 Hospital Admin Dashboard</h1><p>Manage doctors, appointments, and consultation fees.</p></div>", unsafe_allow_html=True)
    hospitals = load_hospitals()
    doctors = load_doctors()
    st.markdown("### Doctors and Fees")
    edited = st.data_editor(doctors, use_container_width=True, hide_index=True, num_rows="dynamic")
    if st.button("💾 Save Doctor Directory", use_container_width=True):
        with get_connection() as conn:
            conn.execute("DELETE FROM doctors")
            for _, doctor in edited.fillna("").iterrows():
                conn.execute("INSERT INTO doctors (doctor_id, hospital_id, name, specialty, availability, fee, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)", (doctor.get("doctor_id") or f"DOC-{uuid.uuid4().hex[:8].upper()}", doctor.get("hospital_id"), doctor.get("name"), doctor.get("specialty"), doctor.get("availability"), float(doctor.get("fee") or 0), int(doctor.get("is_active") or 1)))
            conn.commit()
        st.success("Doctor directory and fees saved.")
    st.markdown("### Appointments")
    consultations = load_consultations()
    st.dataframe(consultations[["request_id", "patient_name", "hospital_name", "doctor_name", "consultation_fee", "payment_status", "request_status", "requested_at"]] if not consultations.empty else pd.DataFrame(), use_container_width=True, hide_index=True)
    st.markdown("### Hospitals")
    st.dataframe(hospitals, use_container_width=True, hide_index=True)

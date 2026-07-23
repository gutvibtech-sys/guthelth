"""Provider-neutral WhatsApp CRM and follow-up workflows for GutVibe."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import pandas as pd
import streamlit as st

DATABASE_FILE = "gutvibe_patients.db"
SUPPORTED_LANGUAGES = ("English", "Malayalam", "Tamil", "Hindi")
FOLLOW_UP_TYPES = ("daily_reminder", "weekly_check", "food_as_medicine", "appointment")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class OutboundMessage:
    to: str
    body: str
    media_url: str = ""
    message_type: str = "text"


@dataclass(frozen=True)
class DeliveryReceipt:
    provider_message_id: str
    status: str = "sent"


class MessagingProvider(Protocol):
    """Adapter contract implemented by Meta, Twilio, or another provider."""

    @property
    def name(self) -> str: ...

    def send(self, message: OutboundMessage) -> DeliveryReceipt: ...


class SandboxMessagingProvider:
    """Safe default provider that records delivery without network access."""

    name = "sandbox"

    def send(self, message: OutboundMessage) -> DeliveryReceipt:
        return DeliveryReceipt(f"sandbox-{uuid.uuid4().hex}")


def get_connection(database_file: str = DATABASE_FILE) -> sqlite3.Connection:
    if not os.path.exists(database_file):
        open(database_file, "a", encoding="utf-8").close()
    os.chmod(database_file, 0o600)
    conn = sqlite3.connect(database_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS whatsapp_contacts (
            patient_id TEXT PRIMARY KEY, phone TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'English',
            opt_in INTEGER NOT NULL DEFAULT 0, opted_in_at TEXT, kiosk_session_id TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS whatsapp_messages (
            message_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, direction TEXT NOT NULL,
            category TEXT NOT NULL, body TEXT NOT NULL, media_url TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL, provider_message_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
            created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS whatsapp_followups (
            followup_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, followup_type TEXT NOT NULL,
            message TEXT NOT NULL, scheduled_for TEXT NOT NULL, recurrence TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'scheduled', last_sent_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS whatsapp_campaigns (
            campaign_id TEXT PRIMARY KEY, name TEXT NOT NULL, audience TEXT NOT NULL,
            message TEXT NOT NULL, language TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


def upsert_contact(patient_id: str, phone: str, language: str = "English", opt_in: bool = False,
                   kiosk_session_id: str = "", database_file: str = DATABASE_FILE) -> None:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}")
    with get_connection(database_file) as conn:
        conn.execute("""INSERT INTO whatsapp_contacts
            (patient_id, phone, language, opt_in, opted_in_at, kiosk_session_id) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(patient_id) DO UPDATE SET phone=excluded.phone, language=excluded.language,
            opt_in=excluded.opt_in, opted_in_at=excluded.opted_in_at, kiosk_session_id=excluded.kiosk_session_id""",
            (patient_id, phone, language, int(opt_in), iso(utc_now()) if opt_in else None, kiosk_session_id))
        conn.commit()


def send_patient_message(patient_id: str, body: str, category: str, provider: MessagingProvider,
                         media_url: str = "", metadata: dict[str, Any] | None = None,
                         database_file: str = DATABASE_FILE) -> str:
    with get_connection(database_file) as conn:
        contact = conn.execute("SELECT * FROM whatsapp_contacts WHERE patient_id = ?", (patient_id,)).fetchone()
        if not contact or not contact["opt_in"]:
            raise PermissionError("An active WhatsApp opt-in is required before messaging this patient.")
        receipt = provider.send(OutboundMessage(contact["phone"], body, media_url, "document" if media_url else "text"))
        message_id = f"MSG-{uuid.uuid4().hex[:12].upper()}"
        conn.execute("""INSERT INTO whatsapp_messages VALUES (?, ?, 'outbound', ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (message_id, patient_id, category, body, media_url, provider.name,
                      receipt.provider_message_id, receipt.status, iso(utc_now()), json.dumps(metadata or {})))
        conn.commit()
    return message_id


def send_assessment_package(patient: dict[str, Any], provider: MessagingProvider,
                            pdf_url: str, qr_download_url: str,
                            database_file: str = DATABASE_FILE) -> list[str]:
    """Send the report summary, PDF, and QR download link after kiosk completion."""
    patient_id = str(patient["patient_id"])
    score = patient.get("gut_health_score") or "not recorded"
    from physiological_engine import DISCLAIMER, wellness_summary
    physiological = wellness_summary(patient_id, database_file)
    physiological_text = ""
    if physiological:
        physiological_text = (
            f" Latest physiological wellness estimate: heart rate "
            f"{physiological.get('heart_rate_bpm', '—')} bpm, signal quality "
            f"{float(physiological.get('signal_quality', 0)):.0%}. {DISCLAIMER}"
        )
    return [
        send_patient_message(patient_id, f"Your GutVibe Wellness Report is ready. Gut health score: {score}/100.{physiological_text}", "wellness_report", provider, database_file=database_file),
        send_patient_message(patient_id, "Your detailed PDF Wellness Report is attached.", "pdf_report", provider, media_url=pdf_url, database_file=database_file),
        send_patient_message(patient_id, f"Download or share your report using this QR link: {qr_download_url}", "qr_download", provider, database_file=database_file),
    ]


def schedule_followup(patient_id: str, followup_type: str, message: str, scheduled_for: datetime,
                      recurrence: str = "", database_file: str = DATABASE_FILE) -> str:
    if followup_type not in FOLLOW_UP_TYPES:
        raise ValueError("Unknown follow-up type")
    followup_id = f"FU-{uuid.uuid4().hex[:12].upper()}"
    with get_connection(database_file) as conn:
        conn.execute("INSERT INTO whatsapp_followups VALUES (?, ?, ?, ?, ?, ?, 'scheduled', NULL)",
                     (followup_id, patient_id, followup_type, message, iso(scheduled_for), recurrence))
        conn.commit()
    return followup_id


def dispatch_due_followups(provider: MessagingProvider, now: datetime | None = None,
                           database_file: str = DATABASE_FILE) -> int:
    now = now or utc_now()
    with get_connection(database_file) as conn:
        due = conn.execute("SELECT * FROM whatsapp_followups WHERE status='scheduled' AND scheduled_for <= ?", (iso(now),)).fetchall()
    sent = 0
    for item in due:
        try:
            send_patient_message(item["patient_id"], item["message"], item["followup_type"], provider,
                                 metadata={"followup_id": item["followup_id"]}, database_file=database_file)
        except PermissionError:
            continue
        next_time = None
        if item["recurrence"] == "daily": next_time = now + timedelta(days=1)
        if item["recurrence"] == "weekly": next_time = now + timedelta(days=7)
        with get_connection(database_file) as conn:
            conn.execute("UPDATE whatsapp_followups SET status=?, last_sent_at=?, scheduled_for=? WHERE followup_id=?",
                         ("scheduled" if next_time else "sent", iso(now), iso(next_time) if next_time else item["scheduled_for"], item["followup_id"]))
            conn.commit()
        sent += 1
    return sent


RESPONSES = {
    "English": "I can help with general wellness, food, sleep, and your GutVibe journey. For urgent symptoms, contact a clinician or emergency service.",
    "Malayalam": "പൊതുവായ ആരോഗ്യം, ഭക്ഷണം, ഉറക്കം എന്നിവയിൽ ഞാൻ സഹായിക്കാം. അടിയന്തര ലക്ഷണങ്ങൾക്ക് ഉടൻ ഡോക്ടറെ സമീപിക്കുക.",
    "Tamil": "பொது நலம், உணவு, தூக்கம் குறித்து உதவ முடியும். அவசர அறிகுறிகளுக்கு உடனே மருத்துவரை அணுகவும்.",
    "Hindi": "मैं सामान्य वेलनेस, भोजन और नींद से जुड़े सवालों में मदद कर सकता हूँ। आपात लक्षणों में तुरंत डॉक्टर से संपर्क करें।",
}


def record_inbound_and_reply(patient_id: str, body: str, provider: MessagingProvider,
                             database_file: str = DATABASE_FILE) -> str:
    """Continue the kiosk conversation using the contact's preferred language."""
    with get_connection(database_file) as conn:
        contact = conn.execute("SELECT * FROM whatsapp_contacts WHERE patient_id=?", (patient_id,)).fetchone()
        if not contact: raise LookupError("Unknown WhatsApp contact")
        conn.execute("INSERT INTO whatsapp_messages VALUES (?, ?, 'inbound', 'patient_question', ?, '', ?, '', 'received', ?, '{}')",
                     (f"MSG-{uuid.uuid4().hex[:12].upper()}", patient_id, body, provider.name, iso(utc_now())))
        conn.commit()
        language = contact["language"]
    reply = RESPONSES[language]
    send_patient_message(patient_id, reply, "ai_assistant", provider, metadata={"language": language, "continuation": "kiosk"}, database_file=database_file)
    return reply


def notify_doctor_referral(patient_id: str, doctor_phone: str, request_id: str,
                           provider: MessagingProvider, database_file: str = DATABASE_FILE) -> str:
    """Record and send a referral alert to a doctor through the same adapter."""
    receipt = provider.send(OutboundMessage(doctor_phone, f"GutVibe referral {request_id} is ready for review (patient {patient_id})."))
    message_id = f"MSG-{uuid.uuid4().hex[:12].upper()}"
    with get_connection(database_file) as conn:
        conn.execute("INSERT INTO whatsapp_messages VALUES (?, ?, 'outbound', 'doctor_referral', ?, '', ?, ?, ?, ?, ?)",
                     (message_id, patient_id, f"Referral {request_id} sent to doctor", provider.name,
                      receipt.provider_message_id, receipt.status, iso(utc_now()), json.dumps({"request_id": request_id, "recipient_role": "doctor"})))
        conn.commit()
    return message_id


def load_communication_history(database_file: str = DATABASE_FILE) -> pd.DataFrame:
    with get_connection(database_file) as conn:
        return pd.read_sql_query("SELECT * FROM whatsapp_messages ORDER BY created_at DESC", conn)


def render_crm_dashboard() -> None:
    st.markdown("<div class='main-header'><h1>💬 WhatsApp CRM</h1><p>Patient conversations, campaigns, follow-ups and delivery analytics</p></div>", unsafe_allow_html=True)
    history = load_communication_history()
    c1, c2, c3 = st.columns(3)
    c1.metric("Messages", len(history))
    c2.metric("Delivered / sent", int(history["status"].isin(["sent", "delivered", "read"]).sum()) if not history.empty else 0)
    c3.metric("Patient replies", int((history["direction"] == "inbound").sum()) if not history.empty else 0)
    tabs = st.tabs(["Communication history", "Campaigns", "Follow-ups", "Analytics"])
    with tabs[0]: st.dataframe(history, use_container_width=True, hide_index=True)
    with tabs[1]:
        with st.form("new_campaign"):
            name = st.text_input("Campaign name")
            audience = st.selectbox("Audience", ["All opted-in patients", "Due for weekly check", "Food as Medicine"])
            language = st.selectbox("Language", SUPPORTED_LANGUAGES)
            message = st.text_area("Approved message/template")
            if st.form_submit_button("Save draft") and name and message:
                with get_connection() as conn:
                    conn.execute("INSERT INTO whatsapp_campaigns VALUES (?, ?, ?, ?, ?, 'draft', ?)", (f"CMP-{uuid.uuid4().hex[:10].upper()}", name, audience, message, language, iso(utc_now())))
                    conn.commit()
                st.success("Campaign saved as draft.")
        with get_connection() as conn: campaigns = pd.read_sql_query("SELECT * FROM whatsapp_campaigns ORDER BY created_at DESC", conn)
        st.dataframe(campaigns, use_container_width=True, hide_index=True)
    with tabs[2]:
        with get_connection() as conn: followups = pd.read_sql_query("SELECT * FROM whatsapp_followups ORDER BY scheduled_for", conn)
        st.dataframe(followups, use_container_width=True, hide_index=True)
    with tabs[3]:
        if history.empty: st.info("Analytics will appear after messages are sent.")
        else: st.bar_chart(history.groupby(["category", "status"]).size().unstack(fill_value=0))

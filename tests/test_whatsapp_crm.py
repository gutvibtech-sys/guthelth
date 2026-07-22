import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from whatsapp_crm import (
    SandboxMessagingProvider,
    dispatch_due_followups,
    get_connection,
    load_communication_history,
    record_inbound_and_reply,
    schedule_followup,
    send_assessment_package,
    send_patient_message,
    upsert_contact,
)


@pytest.fixture
def database(tmp_path):
    path = str(tmp_path / "crm.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE patients (patient_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO patients VALUES ('GV-1')")
    get_connection(path).close()
    return path


def test_assessment_package_requires_consent_and_records_all_assets(database):
    provider = SandboxMessagingProvider()
    with pytest.raises(PermissionError):
        send_patient_message("GV-1", "hello", "wellness_report", provider, database_file=database)

    upsert_contact("GV-1", "+919999999999", "English", True, "kiosk-1", database)
    ids = send_assessment_package(
        {"patient_id": "GV-1", "gut_health_score": 82}, provider,
        "https://example.test/report.pdf", "https://example.test/qr", database,
    )
    history = load_communication_history(database)
    assert len(ids) == 3
    assert set(history["category"]) == {"wellness_report", "pdf_report", "qr_download"}


def test_recurring_followup_dispatches_and_reschedules(database):
    provider = SandboxMessagingProvider()
    upsert_contact("GV-1", "+919999999999", opt_in=True, database_file=database)
    now = datetime(2026, 7, 22, 9, tzinfo=timezone.utc)
    followup_id = schedule_followup("GV-1", "daily_reminder", "Drink water", now - timedelta(minutes=1), "daily", database)
    assert dispatch_due_followups(provider, now, database) == 1
    with get_connection(database) as conn:
        row = conn.execute("SELECT * FROM whatsapp_followups WHERE followup_id=?", (followup_id,)).fetchone()
    assert row["status"] == "scheduled"
    assert row["scheduled_for"] == "2026-07-23T09:00:00Z"


@pytest.mark.parametrize("language", ["English", "Malayalam", "Tamil", "Hindi"])
def test_assistant_continues_kiosk_conversation_in_preferred_language(database, language):
    provider = SandboxMessagingProvider()
    upsert_contact("GV-1", "+919999999999", language, True, "kiosk-1", database)
    reply = record_inbound_and_reply("GV-1", "wellness question", provider, database)
    assert reply
    history = load_communication_history(database)
    assert list(history["direction"]).count("inbound") == 1
    assert "ai_assistant" in set(history["category"])

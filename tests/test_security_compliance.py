from datetime import datetime, timedelta, timezone

import pytest

Fernet = pytest.importorskip("cryptography.fernet").Fernet

from security_compliance import (
    AuditAction,
    AuditLogger,
    AuthenticationService,
    Authorizer,
    BackupService,
    ComplianceStore,
    ConsentService,
    DataCipher,
    DeviceRegistry,
    Role,
    dashboard_metrics,
)


class TestKeys:
    def __init__(self): self.value = Fernet.generate_key()
    def active_key(self): return "test-v1", self.value
    def key(self, key_id):
        if key_id != "test-v1": raise KeyError(key_id)
        return self.value


def services(tmp_path, **auth_options):
    store = ComplianceStore(tmp_path / "security.db")
    audit = AuditLogger(store)
    return store, audit, AuthenticationService(store, audit, **auth_options)


def test_password_login_session_and_audit(tmp_path):
    store, audit, auth = services(tmp_path)
    user_id = auth.create_user("Doctor@One.test", "correct horse battery", Role.DOCTOR)
    session = auth.login("doctor@one.test", "correct horse battery", device_id="kiosk-1")
    assert auth.validate_session(session)["user_id"] == user_id
    with store.connect() as conn:
        event = conn.execute("SELECT * FROM security_audit WHERE action=?", (AuditAction.LOGIN.value,)).fetchone()
    assert event["actor_id"] == user_id
    assert event["event_hash"] != event["previous_hash"]


def test_bad_password_and_mfa_are_denied(tmp_path):
    _, _, auth = services(tmp_path, mfa_verifier=lambda _user, code: code == "654321")
    auth.create_user("admin", "a sufficiently long secret", Role.SUPER_ADMIN, mfa_required=True)
    with pytest.raises(PermissionError): auth.login("admin", "wrong password")
    with pytest.raises(PermissionError): auth.login("admin", "a sufficiently long secret")
    assert auth.login("admin", "a sufficiently long secret", mfa_code="654321")


def test_rbac_is_deny_by_default():
    Authorizer.require(Role.RESEARCH_USER, "research:view_deidentified")
    with pytest.raises(PermissionError): Authorizer.require(Role.RESEARCH_USER, "patient:view")


def test_encryption_is_randomized_and_detects_tampering():
    cipher = DataCipher(TestKeys())
    first, second = cipher.encrypt("patient@example.test"), cipher.encrypt("patient@example.test")
    assert first != second
    assert cipher.decrypt(first) == "patient@example.test"
    with pytest.raises(ValueError): cipher.decrypt(first[:-1] + ("A" if first[-1] != "A" else "B"))


def test_consent_version_history_withdrawal_and_reconsent(tmp_path):
    store, audit, _ = services(tmp_path)
    consent = ConsentService(store, audit)
    first = consent.grant("PAT-1", "care", "1.0", "staff-1", {"method": "signature"})
    consent.withdraw(first, "staff-1", "patient request")
    second = consent.grant("PAT-1", "care", "2.0", "staff-1", {"method": "otp"}, supersedes_id=first)
    history = consent.history("PAT-1", "care")
    assert [row["document_version"] for row in history] == ["1.0", "2.0"]
    assert history[0]["status"] == "superseded"
    assert history[1]["consent_id"] == second and history[1]["status"] == "active"


def test_backup_verification_and_restore(tmp_path):
    store, audit, auth = services(tmp_path)
    auth.create_user("tech", "another long password", Role.TECHNICIAN)
    backups = BackupService(store, tmp_path / "backups")
    backup_id = backups.create()
    assert backups.verify(backup_id)
    restored = tmp_path / "restored.db"
    backups.restore(backup_id, restored)
    with ComplianceStore(restored).connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM security_users").fetchone()[0] == 1


def test_device_and_dashboard_metrics(tmp_path):
    store, audit, auth = services(tmp_path)
    auth.create_user("user", "yet another long secret", Role.HOSPITAL_ADMIN)
    with pytest.raises(PermissionError): auth.login("user", "incorrect secret")
    devices = DeviceRegistry(store)
    device = devices.register("Front kiosk", "hardware-fingerprint", "admin")
    devices.heartbeat(device, "degraded")
    audit.record(AuditAction.VIEW, "patient", actor_id="user", resource_id="PAT-1")
    metrics = dashboard_metrics(store)
    assert metrics["failed_logins"] == 1
    assert metrics["unhealthy_devices"] == 1
    assert metrics["audit_events"] == 2


def test_idle_session_is_revoked(tmp_path):
    store, _, auth = services(tmp_path, idle_minutes=15)
    auth.create_user("doctor", "long enough password!", Role.DOCTOR)
    session = auth.login("doctor", "long enough password!")
    old = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE security_sessions SET last_seen_at=? WHERE session_id=?", (old, session))
    with pytest.raises(PermissionError, match="timed out"): auth.validate_session(session)

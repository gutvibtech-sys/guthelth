"""Security and compliance primitives for GutVibe.

The module is deliberately independent of Streamlit and clinical features.  A
regional compliance package can compose these controls without changing their
storage or application-facing contracts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from cryptography.fernet import Fernet, InvalidToken


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    HOSPITAL_ADMIN = "hospital_admin"
    DOCTOR = "doctor"
    RECEPTION = "reception"
    TECHNICIAN = "technician"
    RESEARCH_USER = "research_user"


class AuditAction(str, Enum):
    LOGIN = "login"
    VIEW = "view"
    EDIT = "edit"
    EXPORT = "export"
    DELETE = "delete"
    REPORT_GENERATION = "report_generation"


ROLE_PERMISSIONS = {
    Role.SUPER_ADMIN: {"*"},
    Role.HOSPITAL_ADMIN: {"patient:view", "patient:edit", "user:manage", "audit:view", "report:generate"},
    Role.DOCTOR: {"patient:view", "patient:edit", "report:generate", "consent:view"},
    Role.RECEPTION: {"patient:create", "patient:view", "consent:manage"},
    Role.TECHNICIAN: {"device:manage", "patient:view_limited"},
    Role.RESEARCH_USER: {"research:view_deidentified", "research:export_deidentified"},
}


class Authorizer:
    """Central deny-by-default RBAC policy boundary."""

    @staticmethod
    def require(role: Role | str, permission: str) -> None:
        try:
            permissions = ROLE_PERMISSIONS[Role(role)]
        except (KeyError, ValueError) as exc:
            raise PermissionError("Unknown role") from exc
        if "*" not in permissions and permission not in permissions:
            raise PermissionError(f"{Role(role).value} cannot perform {permission}")


class KeyProvider(Protocol):
    """Boundary for an HSM, cloud KMS, or local development key source."""

    def active_key(self) -> tuple[str, bytes]: ...
    def key(self, key_id: str) -> bytes: ...


class EnvironmentKeyProvider:
    """Loads a Fernet key from the environment; never persists key material."""

    def __init__(self, variable: str = "GUTVIBE_DATA_KEY", key_id: str = "env-v1"):
        self.variable, self.key_id = variable, key_id

    def active_key(self) -> tuple[str, bytes]:
        value = os.environ.get(self.variable)
        if not value:
            raise RuntimeError(f"Required encryption key {self.variable} is not configured")
        return self.key_id, value.encode("ascii")

    def key(self, key_id: str) -> bytes:
        if not hmac.compare_digest(key_id, self.key_id):
            raise KeyError("Encryption key is unavailable")
        return self.active_key()[1]


class DataCipher:
    """Authenticated field encryption with key identifiers for rotation."""

    def __init__(self, keys: KeyProvider):
        self.keys = keys

    def encrypt(self, plaintext: str) -> str:
        key_id, key = self.keys.active_key()
        token = Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"fernet:{key_id}:{token}"

    def decrypt(self, envelope: str) -> str:
        try:
            algorithm, key_id, token = envelope.split(":", 2)
            if algorithm != "fernet":
                raise ValueError("Unsupported encryption envelope")
            return Fernet(self.keys.key(key_id)).decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Encrypted value failed authentication") from exc


class ComplianceStore:
    """SQLite repository for identity, consent, audit, devices and operations."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS security_users (
              user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
              role TEXT NOT NULL, hospital_id TEXT, active INTEGER NOT NULL DEFAULT 1,
              mfa_required INTEGER NOT NULL DEFAULT 0, failed_logins INTEGER NOT NULL DEFAULT 0,
              locked_until TEXT, last_login_at TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS security_sessions (
              session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT,
              created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL,
              revoked_at TEXT, FOREIGN KEY(user_id) REFERENCES security_users(user_id));
            CREATE TABLE IF NOT EXISTS consent_records (
              consent_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, purpose TEXT NOT NULL,
              document_version TEXT NOT NULL, status TEXT NOT NULL, supersedes_id TEXT,
              captured_by TEXT NOT NULL, evidence_json TEXT NOT NULL, granted_at TEXT NOT NULL,
              withdrawn_at TEXT, withdrawal_reason TEXT);
            CREATE TABLE IF NOT EXISTS security_audit (
              event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, actor_id TEXT,
              action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT,
              outcome TEXT NOT NULL, ip_address TEXT, device_id TEXT, details_json TEXT NOT NULL,
              previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS retention_policies (
              resource_type TEXT PRIMARY KEY, retain_days INTEGER NOT NULL,
              archive_days INTEGER NOT NULL, legal_basis TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS retention_items (
              item_id TEXT PRIMARY KEY, resource_type TEXT NOT NULL, created_at TEXT NOT NULL,
              archived_at TEXT, deleted_at TEXT, deletion_proof TEXT);
            CREATE TABLE IF NOT EXISTS registered_devices (
              device_id TEXT PRIMARY KEY, name TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL,
              kiosk_mode INTEGER NOT NULL, status TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              registered_by TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS backup_runs (
              backup_id TEXT PRIMARY KEY, path TEXT NOT NULL, status TEXT NOT NULL,
              checksum TEXT, created_at TEXT NOT NULL, verified_at TEXT, restored_at TEXT);
            """)
        try:
            os.chmod(self.database, 0o600)
        except OSError:
            pass


class PasswordHasher:
    """Versioned scrypt hashes with constant-time verification."""

    @staticmethod
    def hash(password: str) -> str:
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"

    @staticmethod
    def verify(password: str, encoded: str) -> bool:
        try:
            algorithm, n, r, p, salt, expected = encoded.split("$")
            if algorithm != "scrypt":
                return False
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
            return hmac.compare_digest(actual, bytes.fromhex(expected))
        except (ValueError, TypeError):
            return False


class AuditLogger:
    """Append-only, hash-chained audit trail (redact PHI before details)."""

    def __init__(self, store: ComplianceStore): self.store = store

    def record(self, action: AuditAction | str, resource_type: str, *, actor_id: str | None = None,
               resource_id: str | None = None, outcome: str = "success", ip_address: str | None = None,
               device_id: str | None = None, details: Mapping[str, Any] | None = None) -> str:
        action = AuditAction(action).value
        event_id, occurred_at = secrets.token_hex(16), utc_now()
        safe_details = json.dumps(details or {}, sort_keys=True, separators=(",", ":"), default=str)
        with self.store.connect() as conn:
            row = conn.execute("SELECT event_hash FROM security_audit ORDER BY rowid DESC LIMIT 1").fetchone()
            previous = row[0] if row else "GENESIS"
            payload = "|".join(str(v or "") for v in (event_id, occurred_at, actor_id, action, resource_type,
                                                        resource_id, outcome, ip_address, device_id, safe_details, previous))
            event_hash = hashlib.sha256(payload.encode()).hexdigest()
            conn.execute("INSERT INTO security_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (event_id, occurred_at, actor_id, action, resource_type, resource_id, outcome,
                          ip_address, device_id, safe_details, previous, event_hash))
        return event_id


class AuthenticationService:
    """Account, lockout, MFA hook, and idle/absolute session controls."""

    def __init__(self, store: ComplianceStore, audit: AuditLogger, idle_minutes: int = 15,
                 session_hours: int = 8, mfa_verifier: Callable[[str, str], bool] | None = None):
        self.store, self.audit = store, audit
        self.idle_minutes, self.session_hours, self.mfa_verifier = idle_minutes, session_hours, mfa_verifier

    def create_user(self, username: str, password: str, role: Role, *, hospital_id: str | None = None,
                    mfa_required: bool = False) -> str:
        user_id = secrets.token_hex(16)
        with self.store.connect() as conn:
            conn.execute("INSERT INTO security_users VALUES (?,?,?,?,?,1,?,0,NULL,NULL,?)",
                         (user_id, username.strip().casefold(), PasswordHasher.hash(password), role.value,
                          hospital_id, int(mfa_required), utc_now()))
        return user_id

    def login(self, username: str, password: str, *, mfa_code: str | None = None,
              device_id: str | None = None, ip_address: str | None = None) -> str:
        now = datetime.now(timezone.utc)
        with self.store.connect() as conn:
            user = conn.execute("SELECT * FROM security_users WHERE username=?", (username.strip().casefold(),)).fetchone()
            valid = bool(user and user["active"] and PasswordHasher.verify(password, user["password_hash"]))
            locked = bool(user and user["locked_until"] and datetime.fromisoformat(user["locked_until"]) > now)
            if not valid or locked:
                if user:
                    failures = user["failed_logins"] + 1
                    lock_until = (now + timedelta(minutes=15)).replace(microsecond=0).isoformat() if failures >= 5 else None
                    conn.execute("UPDATE security_users SET failed_logins=?, locked_until=? WHERE user_id=?",
                                 (failures, lock_until, user["user_id"]))
                conn.commit()
                self.audit.record(AuditAction.LOGIN, "session", actor_id=user["user_id"] if user else None,
                                  outcome="denied", ip_address=ip_address, device_id=device_id)
                raise PermissionError("Invalid credentials or account unavailable")
            if user["mfa_required"] and (not self.mfa_verifier or not mfa_code or
                                          not self.mfa_verifier(user["user_id"], mfa_code)):
                conn.commit()
                self.audit.record(AuditAction.LOGIN, "session", actor_id=user["user_id"], outcome="mfa_denied")
                raise PermissionError("Multi-factor authentication required")
            session_id = secrets.token_urlsafe(32)
            created = now.replace(microsecond=0)
            conn.execute("UPDATE security_users SET failed_logins=0, locked_until=NULL, last_login_at=? WHERE user_id=?",
                         (created.isoformat(), user["user_id"]))
            conn.execute("INSERT INTO security_sessions VALUES (?,?,?,?,?,?,NULL)",
                         (session_id, user["user_id"], device_id, created.isoformat(), created.isoformat(),
                          (created + timedelta(hours=self.session_hours)).isoformat()))
        self.audit.record(AuditAction.LOGIN, "session", actor_id=user["user_id"], device_id=device_id)
        return session_id

    def validate_session(self, session_id: str) -> sqlite3.Row:
        now = datetime.now(timezone.utc)
        with self.store.connect() as conn:
            row = conn.execute("SELECT s.*,u.role,u.active FROM security_sessions s JOIN security_users u USING(user_id) WHERE session_id=?",
                               (session_id,)).fetchone()
            if not row or row["revoked_at"] or not row["active"] or datetime.fromisoformat(row["expires_at"]) <= now:
                raise PermissionError("Session expired")
            if datetime.fromisoformat(row["last_seen_at"]) + timedelta(minutes=self.idle_minutes) <= now:
                conn.execute("UPDATE security_sessions SET revoked_at=? WHERE session_id=?", (utc_now(), session_id))
                raise PermissionError("Session timed out")
            conn.execute("UPDATE security_sessions SET last_seen_at=? WHERE session_id=?", (utc_now(), session_id))
            return row


class ConsentService:
    def __init__(self, store: ComplianceStore, audit: AuditLogger): self.store, self.audit = store, audit

    def grant(self, patient_id: str, purpose: str, document_version: str, captured_by: str,
              evidence: Mapping[str, Any], supersedes_id: str | None = None) -> str:
        consent_id = secrets.token_hex(16)
        with self.store.connect() as conn:
            if supersedes_id:
                old = conn.execute("SELECT * FROM consent_records WHERE consent_id=? AND patient_id=?",
                                   (supersedes_id, patient_id)).fetchone()
                if not old: raise ValueError("Consent to supersede does not exist")
                conn.execute("UPDATE consent_records SET status='superseded' WHERE consent_id=?", (supersedes_id,))
            conn.execute("INSERT INTO consent_records VALUES (?,?,?,?,? ,?,?,?,?,NULL,NULL)",
                         (consent_id, patient_id, purpose, document_version, "active", supersedes_id,
                          captured_by, json.dumps(evidence, sort_keys=True), utc_now()))
        self.audit.record(AuditAction.EDIT, "consent", actor_id=captured_by, resource_id=consent_id,
                          details={"purpose": purpose, "document_version": document_version})
        return consent_id

    def withdraw(self, consent_id: str, actor_id: str, reason: str) -> None:
        with self.store.connect() as conn:
            result = conn.execute("UPDATE consent_records SET status='withdrawn', withdrawn_at=?, withdrawal_reason=? WHERE consent_id=? AND status='active'",
                                  (utc_now(), reason, consent_id))
            if result.rowcount != 1: raise ValueError("Only active consent can be withdrawn")
        self.audit.record(AuditAction.EDIT, "consent", actor_id=actor_id, resource_id=consent_id,
                          details={"change": "withdrawn"})

    def history(self, patient_id: str, purpose: str) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM consent_records WHERE patient_id=? AND purpose=? ORDER BY granted_at", (patient_id, purpose))]


class DeviceRegistry:
    def __init__(self, store: ComplianceStore): self.store = store

    def register(self, name: str, fingerprint: str, actor_id: str, kiosk_mode: bool = True) -> str:
        device_id = secrets.token_hex(16)
        with self.store.connect() as conn:
            conn.execute("INSERT INTO registered_devices VALUES (?,?,?,?,?,?,?)",
                         (device_id, name, hashlib.sha256(fingerprint.encode()).hexdigest(), int(kiosk_mode),
                          "healthy", utc_now(), actor_id))
        return device_id

    def heartbeat(self, device_id: str, status: str = "healthy") -> None:
        if status not in {"healthy", "degraded", "offline", "revoked"}: raise ValueError("Invalid device status")
        with self.store.connect() as conn:
            if conn.execute("UPDATE registered_devices SET status=?,last_seen_at=? WHERE device_id=?",
                            (status, utc_now(), device_id)).rowcount != 1: raise KeyError("Unknown device")


class BackupService:
    """Atomic SQLite backup, checksum verification, and explicit restore."""

    def __init__(self, store: ComplianceStore, backup_directory: str | Path):
        self.store, self.directory = store, Path(backup_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        try: os.chmod(self.directory, 0o700)
        except OSError: pass

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""): digest.update(chunk)
        return digest.hexdigest()

    def create(self) -> str:
        backup_id = secrets.token_hex(12)
        target = self.directory / f"gutvibe-{backup_id}.sqlite"
        with self.store.connect() as source, sqlite3.connect(target) as destination: source.backup(destination)
        os.chmod(target, 0o600)
        checksum = self._checksum(target)
        with self.store.connect() as conn:
            conn.execute("INSERT INTO backup_runs VALUES (?,?,?,?,?,NULL,NULL)",
                         (backup_id, str(target), "created", checksum, utc_now()))
        return backup_id

    def verify(self, backup_id: str) -> bool:
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM backup_runs WHERE backup_id=?", (backup_id,)).fetchone()
            if not row: raise KeyError("Unknown backup")
            valid = Path(row["path"]).is_file() and hmac.compare_digest(self._checksum(Path(row["path"])), row["checksum"])
            if valid:
                with sqlite3.connect(row["path"]) as backup: valid = backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            conn.execute("UPDATE backup_runs SET status=?,verified_at=? WHERE backup_id=?",
                         ("verified" if valid else "invalid", utc_now(), backup_id))
        return valid

    def restore(self, backup_id: str, destination: str | Path) -> None:
        if not self.verify(backup_id): raise ValueError("Backup verification failed")
        with self.store.connect() as conn: row = conn.execute("SELECT path FROM backup_runs WHERE backup_id=?", (backup_id,)).fetchone()
        destination = Path(destination)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary: temp_path = Path(temporary.name)
        try:
            shutil.copy2(row["path"], temp_path)
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)
        with self.store.connect() as conn:
            conn.execute("UPDATE backup_runs SET restored_at=? WHERE backup_id=?", (utc_now(), backup_id))


class RetentionService:
    def __init__(self, store: ComplianceStore, audit: AuditLogger): self.store, self.audit = store, audit

    def set_policy(self, resource_type: str, retain_days: int, archive_days: int, legal_basis: str) -> None:
        if retain_days < 1 or archive_days < 0: raise ValueError("Retention periods must be non-negative")
        with self.store.connect() as conn:
            conn.execute("INSERT INTO retention_policies VALUES (?,?,?,?,?) ON CONFLICT(resource_type) DO UPDATE SET retain_days=excluded.retain_days,archive_days=excluded.archive_days,legal_basis=excluded.legal_basis,updated_at=excluded.updated_at",
                         (resource_type, retain_days, archive_days, legal_basis, utc_now()))

    def register_item(self, item_id: str, resource_type: str, created_at: str | None = None) -> None:
        with self.store.connect() as conn:
            conn.execute("INSERT INTO retention_items VALUES (?,?,?,NULL,NULL,NULL)",
                         (item_id, resource_type, created_at or utc_now()))

    def mark_archived(self, item_id: str, actor_id: str) -> None:
        with self.store.connect() as conn:
            result = conn.execute("UPDATE retention_items SET archived_at=? WHERE item_id=? AND archived_at IS NULL AND deleted_at IS NULL",
                                  (utc_now(), item_id))
            if result.rowcount != 1: raise ValueError("Item is unavailable for archival")
        self.audit.record(AuditAction.EDIT, "retention_item", actor_id=actor_id, resource_id=item_id,
                          details={"change": "archived"})

    def mark_securely_deleted(self, item_id: str, actor_id: str, deletion_proof: str) -> None:
        """Record deletion only after the underlying storage adapter confirms it."""
        if not deletion_proof.strip(): raise ValueError("Deletion proof is required")
        with self.store.connect() as conn:
            result = conn.execute("UPDATE retention_items SET deleted_at=?,deletion_proof=? WHERE item_id=? AND deleted_at IS NULL",
                                  (utc_now(), deletion_proof, item_id))
            if result.rowcount != 1: raise ValueError("Item is unavailable for deletion")
        self.audit.record(AuditAction.DELETE, "retention_item", actor_id=actor_id, resource_id=item_id,
                          details={"deletion_proof_reference": deletion_proof})

    def due(self, resource_type: str, now: datetime | None = None) -> dict[str, list[str]]:
        now = now or datetime.now(timezone.utc)
        with self.store.connect() as conn:
            policy = conn.execute("SELECT * FROM retention_policies WHERE resource_type=?", (resource_type,)).fetchone()
            if not policy: raise KeyError("No retention policy")
            items = conn.execute("SELECT * FROM retention_items WHERE resource_type=? AND deleted_at IS NULL", (resource_type,)).fetchall()
        archive, delete = [], []
        for item in items:
            created = datetime.fromisoformat(item["created_at"])
            if created + timedelta(days=policy["retain_days"] + policy["archive_days"]) <= now: delete.append(item["item_id"])
            elif not item["archived_at"] and created + timedelta(days=policy["retain_days"]) <= now: archive.append(item["item_id"])
        return {"archive": archive, "delete": delete}


def dashboard_metrics(store: ComplianceStore) -> dict[str, Any]:
    """Read-only security dashboard projection."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).replace(microsecond=0).isoformat()
    with store.connect() as conn:
        return {
            "active_users": conn.execute("SELECT COUNT(DISTINCT user_id) FROM security_sessions WHERE revoked_at IS NULL AND last_seen_at>=?", (cutoff,)).fetchone()[0],
            "failed_logins": conn.execute("SELECT COALESCE(SUM(failed_logins),0) FROM security_users").fetchone()[0],
            "audit_events": conn.execute("SELECT COUNT(*) FROM security_audit").fetchone()[0],
            "unhealthy_devices": conn.execute("SELECT COUNT(*) FROM registered_devices WHERE status!='healthy'").fetchone()[0],
            "latest_backup": dict(conn.execute("SELECT status,created_at,verified_at FROM backup_runs ORDER BY created_at DESC LIMIT 1").fetchone() or {}),
        }


def render_security_dashboard(store: ComplianceStore) -> None:
    """Optional Streamlit adapter; authorization must occur before calling it."""
    import streamlit as st
    metrics = dashboard_metrics(store)
    st.header("🔐 Security & Compliance")
    columns = st.columns(4)
    columns[0].metric("Active users", metrics["active_users"])
    columns[1].metric("Failed logins", metrics["failed_logins"])
    columns[2].metric("Audit events", metrics["audit_events"])
    columns[3].metric("Unhealthy devices", metrics["unhealthy_devices"])
    st.subheader("Backup status")
    st.json(metrics["latest_backup"] or {"status": "No backup recorded"})

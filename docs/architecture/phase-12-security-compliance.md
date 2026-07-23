# Phase 12 — Security, Privacy, Consent and Compliance

## Scope and safety boundary

Phase 12 introduces application-independent controls in `security_compliance.py`.
They are a baseline, not a certification. A production launch still requires a
threat model, DPIA, legal review, penetration test, incident-response exercises,
infrastructure controls, staff training and validation of every deployment.

The existing Streamlit prototype does **not** become safe merely by importing
this module. Its routes must be placed behind `AuthenticationService`; every
handler must call `Authorizer.require`; every patient operation must emit an
audit event; and TLS, network segmentation and secret management must be
provided by the deployment platform.

## Architecture

```text
Browser / locked kiosk
        │ TLS + session cookie (Secure, HttpOnly, SameSite=Strict)
Identity gateway ── MFA provider (TOTP/WebAuthn/enterprise IdP)
        │ verified principal, role, hospital scope
Authorization service (deny by default)
        ├── Consent service ── versioned evidence / withdrawal / re-consent
        ├── Clinical services ── field cipher ── patient database
        ├── Audit service ── append-only hash chain ── immutable log sink
        ├── Device registry ── heartbeat / kiosk policy
        └── Operations ── retention jobs / encrypted backup vault
                                  │
                         HSM / cloud KMS (keys never in database)
```

`KeyProvider` is the stable key-management interface. The supplied environment
adapter is for controlled deployments; production should implement it with an
HSM or KMS, key access policy, rotation, revocation and access logging. Encrypted
fields use authenticated Fernet envelopes containing a key ID, permitting
rotation without schema changes. TLS is required in transit.

Passwords are salted scrypt hashes. Login has generic errors, lockout after five
failures, optional MFA verification, random sessions, absolute expiry and
15-minute idle logout. MFA is an injected verifier so TOTP, WebAuthn or an IdP
can be selected without changing authentication storage. Never store an MFA
shared secret without KMS-backed encryption.

## Roles and minimum access

| Role | Intended access |
|---|---|
| Super Admin | Global control plane; prohibit routine patient access operationally |
| Hospital Admin | Hospital users, audit, reports and patient administration |
| Doctor | Assigned patient care, consent view and reports |
| Reception | Registration and consent capture |
| Technician | Registered device operations and limited patient context |
| Research User | Approved, de-identified datasets only |

The code provides coarse permissions. Production policy must additionally check
tenant/hospital, care relationship, purpose, current consent and field-level
rules. Research exports require documented approval and re-identification-risk
review. No role receives access implicitly.

## Consent workflow

1. Present the current, localized notice and capture purpose-specific affirmative
   action, document version, timestamp, operator and evidence (signature/OTP
   reference). Do not put signatures or patient data into audit metadata.
2. Create an active consent record. Preserve prior versions.
3. On notice/purpose changes, request re-consent and link the new record through
   `supersedes_id`; processing requiring the new terms remains disabled until
   granted.
4. Withdrawal changes active consent immediately, records time and reason, and
   triggers downstream suppression/deletion review. Withdrawal does not erase
   evidence that must be retained for legal accountability.
5. A processing service must check active, purpose-matching consent immediately
   before use; consent storage alone does not enforce processing.

## Audit and monitoring

Login, view, edit, export, delete and report-generation actions are enumerated.
Events contain actor, resource reference, outcome, device and optional network
context, and form a SHA-256 hash chain. Details must contain no passwords,
tokens, keys or unnecessary health data. Ship events to append-only/WORM storage
with restricted administrator access, alert on chain discontinuity, repeated
failed login, bulk export, privilege changes and device degradation, and protect
audit records under a separate retention policy.

The dashboard projection exposes active users, cumulative failed attempts, audit
volume, unhealthy devices and latest backup verification. Call its Streamlit
adapter only after checking `audit:view` and hospital scope.

## Retention, archival and secure deletion

Each resource type needs a documented legal basis, active retention period and
archive period. A scheduled job obtains candidates from `RetentionService.due`,
moves archive candidates into encrypted restricted storage, applies legal holds,
then deletes eligible primary data and all derived copies. Record a deletion
proof and `delete` audit event. For SSDs and managed databases, use cryptographic
erasure/key destruction and provider-certified deletion rather than assuming
file overwrite works. Backup expiry must eventually propagate deletions while
preserving documented statutory exceptions.

## Backup and recovery runbook

1. Schedule SQLite online backups to a separate, encrypted, access-controlled
   location; the included local directory is a development adapter.
2. Verify every backup checksum and SQLite integrity automatically. Alert on a
   missed or invalid run. Replicate off-site according to approved residency.
3. Quarterly, select a verified backup, provision an isolated recovery system,
   restore it, rotate restored credentials, run integrity/application checks and
   document achieved RPO/RTO. Never overwrite production during a drill.
4. During an incident, authorize restoration through two-person change control,
   preserve evidence, stop writers, restore to a temporary path, validate, swap
   atomically, reconcile post-backup transactions, and record approval/results.

## Device controls

Provision kiosks with a unique device identity and hashed hardware fingerprint;
disable OS navigation, removable media, developer shortcuts and local caching;
use full-disk encryption, secure boot and allow-listed outbound traffic. The
registry tracks kiosk mode and health heartbeat. Revoke lost/tampered devices.
Sessions automatically idle-timeout, but the kiosk shell must also clear browser
state and return to its welcome screen.

## India DPDP Act readiness

Map each purpose to a lawful basis; give a clear, accessible notice; use
purpose-specific consent where consent is relied on; provide easy withdrawal;
implement data-principal access/correction/erasure/grievance processes; verify
parental consent where applicable; minimize collection; maintain accuracy and
reasonable security safeguards; manage processors contractually; and operate a
breach-assessment and notification process. Determine Significant Data
Fiduciary obligations and cross-border restrictions with Indian counsel. Record
the notice/version and request workflow, not merely a checkbox.

This is an engineering readiness map, not legal advice or a claim of DPDP
compliance. Keep obligations in regional policy/configuration packages. Future
HIPAA, GDPR and other packages can add policy evaluators, notice templates,
retention schedules and reporting over the same identity, consent, audit,
encryption, device and backup interfaces.

## Production checklist

- Use an external IdP/MFA or independently reviewed implementation; bootstrap the
  first administrator out of band and eliminate default credentials.
- Put secrets in KMS/HSM, rotate them, test old-key decrypt/re-encryption, and
  separate key administrators from database administrators.
- Enforce authorization and audit at service boundaries, not only in UI menus.
- Configure rate limiting, CSRF protection, secure cookies, CSP/HSTS, TLS,
  dependency/container scanning and signed releases.
- Run backup/restore and retention jobs from an authenticated scheduler, export
  metrics to monitoring, and test failure alerts.
- Complete DPIA, data inventory/flow map, processor register, incident response,
  business continuity, access reviews and independent security testing.

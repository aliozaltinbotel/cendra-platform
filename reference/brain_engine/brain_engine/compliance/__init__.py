"""GDPR / data-subject-rights compliance layer for Brain Engine.

The compliance module owns the concerns the rest of the engine cannot
solve correctly on its own:

* **PII detection** — country-specific identifier patterns the engine
  must redact, mask, or hash before persisting them past the in-memory
  request span.
* **Retention** — TTL by data class, so an episodic event does not live
  longer than its retention contract.
* **Audit** — an immutable log of every access to data classified as
  ``Sensitive`` or higher, sufficient for a regulator's right-to-know
  request.
* **Consent** — registry of guest opt-ins for purposes beyond contract
  performance (marketing, AI personalisation, pattern mining).
* **Data-subject rights** — coordinator that fans out access /
  erasure / portability requests across every memory tier and emits
  an immutable :class:`DSRReport`.
* **Encryption** — HKDF-blake2b key derivation + AES-GCM encryptor for
  at-rest sensitive payloads.

Every public surface is intentionally synchronous and deterministic so
guardrails (ADR-0005) can call it inside the cascade without paying an
async hop.

Example::

    from brain_engine.compliance import PIIDetector, redact

    detector = PIIDetector()
    spans = detector.scan("Mi DNI es 12345678Z, mi NIE Y1234567Z.")
    redacted = redact("Mi DNI es 12345678Z, mi NIE Y1234567Z.", spans)
"""

from brain_engine.compliance.audit import (
    AuditEvent,
    AuditLogger,
    InMemoryAuditLogger,
)
from brain_engine.compliance.consent_store import (
    ConsentPurpose,
    ConsentRecord,
    ConsentSource,
    ConsentStore,
    InMemoryConsentStore,
    has_consent,
)
from brain_engine.compliance.data_subject_rights import (
    DataSubjectRightsCoordinator,
    DSRReport,
    DSRRequest,
    DSRRequestType,
    DSRStatus,
    TierEraser,
    TierExporter,
    TierResult,
    new_request_id,
)
from brain_engine.compliance.encryption import (
    AESGCMEncryptor,
    EncryptionKeyProvider,
    Encryptor,
    EnvKeyProvider,
    KeyDerivation,
    KeyHandle,
)
from brain_engine.compliance.pii_detector import (
    PIIDetector,
    PIIMatch,
    PIIType,
)
from brain_engine.compliance.redactor import RedactionStrategy, redact
from brain_engine.compliance.retention import (
    DataClass,
    RetentionManager,
    RetentionPolicy,
)

__all__ = [
    "AESGCMEncryptor",
    "AuditEvent",
    "AuditLogger",
    "ConsentPurpose",
    "ConsentRecord",
    "ConsentSource",
    "ConsentStore",
    "DSRReport",
    "DSRRequest",
    "DSRRequestType",
    "DSRStatus",
    "DataClass",
    "DataSubjectRightsCoordinator",
    "EncryptionKeyProvider",
    "Encryptor",
    "EnvKeyProvider",
    "InMemoryAuditLogger",
    "InMemoryConsentStore",
    "KeyDerivation",
    "KeyHandle",
    "PIIDetector",
    "PIIMatch",
    "PIIType",
    "RedactionStrategy",
    "RetentionManager",
    "RetentionPolicy",
    "TierEraser",
    "TierExporter",
    "TierResult",
    "has_consent",
    "new_request_id",
    "redact",
]

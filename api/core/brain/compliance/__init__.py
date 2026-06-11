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

    from core.brain.compliance import PIIDetector, redact

    detector = PIIDetector()
    spans = detector.scan("Mi DNI es 12345678Z, mi NIE Y1234567Z.")
    redacted = redact("Mi DNI es 12345678Z, mi NIE Y1234567Z.", spans)
"""

from core.brain.compliance.art12_audit import (
    Art12AuditLogger,
    InMemoryArt12AuditLogger,
    SQLAlchemyArt12AuditLogger,
)
from core.brain.compliance.audit import (
    AuditEvent,
    AuditLogger,
    InMemoryAuditLogger,
)
from core.brain.compliance.consent_store import (
    ConsentPurpose,
    ConsentRecord,
    ConsentSource,
    ConsentStore,
    InMemoryConsentStore,
    has_consent,
)
from core.brain.compliance.data_subject_rights import (
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
from core.brain.compliance.encryption import (
    AESGCMEncryptor,
    EncryptionKeyProvider,
    Encryptor,
    EnvKeyProvider,
    KeyDerivation,
    KeyHandle,
)
from core.brain.compliance.pii_detector import (
    PIIDetector,
    PIIMatch,
    PIIType,
)
from core.brain.compliance.redactor import RedactionStrategy, redact
from core.brain.compliance.retention import (
    DataClass,
    RetentionManager,
    RetentionPolicy,
)

__all__ = [
    "AESGCMEncryptor",
    "Art12AuditLogger",
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
    "InMemoryArt12AuditLogger",
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
    "SQLAlchemyArt12AuditLogger",
    "TierEraser",
    "TierExporter",
    "TierResult",
    "has_consent",
    "new_request_id",
    "redact",
]

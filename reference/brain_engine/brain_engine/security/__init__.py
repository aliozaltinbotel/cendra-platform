"""Security primitives for Brain Engine.

The security layer covers the four concerns the rest of the engine
cannot solve from inside the cognitive pipeline:

* **Rate limiting** — per-tenant token bucket with separate buckets
  for *dangerous* actions (`charge_guest`, `release_access_code`,
  `skill_evolution`).  Hard caps protect against runaway behaviour
  even when per-tenant trust is high.
* **Prompt-injection detection** — rule-based classifier that flags
  jailbreak templates, role-rewrite attempts, and instruction
  smuggling.  Decision is advisory; the cascade decides what to do
  with a "suspicious" verdict.
* **Jailbreak classification** — narrower companion that scores
  against named jailbreak families (DAN, dev-mode, grandma exploit,
  encoded payloads).  Cascade combines both verdicts.
* **Tenant isolation** — pre-commit validator that scans a Skill /
  PatternRule body for identifiers from a *different* tenant before
  the rule is allowed into procedural memory.

Plus secret resolution under :mod:`secret_provider` so every reader
of credentials goes through one rotation-friendly seam.

Reference: ``brain_engine_advisory.md`` §9.1-9.4.
"""

from brain_engine.security.jailbreak_classifier import (
    JailbreakClassifier,
    JailbreakFamily,
    JailbreakVerdict,
)
from brain_engine.security.prompt_injection import (
    InjectionVerdict,
    PromptInjectionDetector,
)
from brain_engine.security.rate_limiter import (
    RateLimitDecision,
    TenantRateLimiter,
    TenantTier,
)
from brain_engine.security.secret_provider import (
    CompositeSecretProvider,
    EnvSecretProvider,
    KeyVaultSecretProvider,
    SecretProvider,
)
from brain_engine.security.tenant_isolation import (
    IsolationViolation,
    TenantIsolationValidator,
)

__all__ = [
    "CompositeSecretProvider",
    "EnvSecretProvider",
    "InjectionVerdict",
    "IsolationViolation",
    "JailbreakClassifier",
    "JailbreakFamily",
    "JailbreakVerdict",
    "KeyVaultSecretProvider",
    "PromptInjectionDetector",
    "RateLimitDecision",
    "SecretProvider",
    "TenantIsolationValidator",
    "TenantRateLimiter",
    "TenantTier",
]

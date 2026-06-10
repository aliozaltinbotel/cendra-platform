# CENDRA-HOOK(T4): Art. 50 disclosure + PII redaction moderation module.
"""Cendra brain moderation extension (touchpoint T4 — zero upstream edits).

Discovered by Dify's code-based extension scanner like any built-in
moderation module, so chat apps can enable it per-app from the console.
Two responsibilities (PORTING_MAP Batch 5):

- **EU AI Act Art. 50 disclosure** — appends the locale-appropriate
  "you are interacting with an AI system" disclosure to outputs (once
  per text; the kernel's :func:`disclosure_for` falls back to English).
- **PII redaction** — masks detected PII spans in user inputs and
  model outputs using the kernel detector/redactor (MASK strategy; the
  HASH strategy needs the per-tenant secret provider, Batch 5 service
  wiring).

Config keys (set per app in the console's moderation settings):
``locale`` (default ``en``), ``redact_inputs`` / ``redact_outputs``
(default true), ``disclose`` (default true).
"""

from typing import Any, override

from core.brain.compliance.art50_disclosure import disclosure_for
from core.brain.compliance.pii_detector import PIIDetector
from core.brain.compliance.redactor import redact
from core.moderation.base import Moderation, ModerationAction, ModerationInputsResult, ModerationOutputsResult

_detector = PIIDetector()


class CendraBrainModeration(Moderation):
    name: str = "cendra_brain"

    @classmethod
    @override
    def validate_config(cls, tenant_id: str, config: dict[str, Any]):
        cls._validate_inputs_and_outputs_config(config, False)

    @override
    def moderation_for_inputs(self, inputs: dict[str, Any], query: str = "") -> ModerationInputsResult:
        config = self.config or {}
        if not config.get("redact_inputs", True):
            return ModerationInputsResult(flagged=False, action=ModerationAction.DIRECT_OUTPUT)
        redacted_inputs = {key: self._redact_value(value) for key, value in inputs.items()}
        redacted_query = self._redact_text(query)
        changed = redacted_query != query or redacted_inputs != inputs
        return ModerationInputsResult(
            flagged=changed,
            action=ModerationAction.OVERRIDDEN,
            inputs=redacted_inputs,
            query=redacted_query,
        )

    @override
    def moderation_for_outputs(self, text: str) -> ModerationOutputsResult:
        config = self.config or {}
        out = text
        if config.get("redact_outputs", True):
            out = self._redact_text(out)
        if config.get("disclose", True):
            disclosure = disclosure_for(str(config.get("locale", "en")))
            if disclosure.text not in out:
                out = f"{out}\n\n{disclosure.text}"
        return ModerationOutputsResult(
            flagged=out != text,
            action=ModerationAction.OVERRIDDEN,
            text=out,
        )

    @staticmethod
    def _redact_text(text: str) -> str:
        if not text:
            return text
        matches = _detector.scan(text)
        if not matches:
            return text
        return redact(text, matches)

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        return value

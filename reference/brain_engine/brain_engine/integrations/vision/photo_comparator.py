"""
Photo Comparator - uses GPT-4o Vision to compare before/after property photos
and identify damages for Airbnb claims.

Routes exclusively through the tenant's Azure OpenAI deployment.
Public ``api.openai.com`` is never called.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from brain_engine.models.azure_routing import (
    AzureOpenAIConfig,
    load_azure_openai_config,
)

logger = logging.getLogger(__name__)

COMPARISON_SYSTEM_PROMPT = """\
You are a professional property damage assessor. You will be shown two photos \
of the same area in a rental property: a BEFORE photo (taken before a guest's \
stay) and an AFTER photo (taken after checkout).

Analyze both photos carefully and identify any damages, wear, or changes. \
For each issue found, provide:
1. A clear description of the damage
2. A severity score from 1-10 (1=cosmetic scratch, 10=structural damage)
3. The category (furniture, flooring, wall, appliance, fixture, electronics, \
linens, plumbing, exterior, other)
4. Estimated repair/replacement cost in USD

Respond ONLY with valid JSON in this exact format:
{
  "damages": [
    {
      "description": "...",
      "severity": 7,
      "category": "furniture",
      "estimated_cost": 150.00,
      "location": "living room, left side"
    }
  ],
  "overall_severity": 5.0,
  "summary": "One-paragraph summary of findings",
  "no_damage_detected": false
}

If no damage is detected, set "no_damage_detected" to true and return an \
empty damages list.
"""


@dataclass(frozen=True)
class DamageDetail:
    """A single damage identified during photo comparison."""

    description: str
    severity: int
    category: str
    estimated_cost: float
    location: str = ""


@dataclass(frozen=True)
class ComparisonResult:
    """Result of comparing before and after property photos."""

    damages: list[DamageDetail] = field(default_factory=list)
    overall_severity: float = 0.0
    summary: str = ""
    no_damage_detected: bool = True
    total_estimated_cost: float = 0.0
    raw_response: dict[str, Any] = field(default_factory=dict, repr=False)


class PhotoComparatorError(Exception):
    """Raised when photo comparison fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PhotoComparator:
    """
    Compares before/after property photos using GPT-4o Vision to detect damages.

    Supports both local file paths and URLs.

    Usage::

        comparator = PhotoComparator()
        result = await comparator.compare_photos(
            before_path="data/photos/before/kitchen.jpg",
            after_path="data/photos/after/kitchen.jpg",
        )
        if not result.no_damage_detected:
            for damage in result.damages:
                print(f"{damage.description}: severity {damage.severity}")
    """

    def __init__(
        self,
        *,
        max_tokens: int = 2000,
        timeout: float = 120.0,
        azure_config: AzureOpenAIConfig | None = None,
    ) -> None:
        # Routes through the tenant's Azure OpenAI Vision deployment
        # at ``{endpoint}/openai/deployments/{deployment}/chat/
        # completions?api-version=...`` with the ``api-key`` header.
        cfg = azure_config or load_azure_openai_config()
        if not cfg.is_complete():
            raise PhotoComparatorError(
                "Azure OpenAI config is incomplete; "
                "set AZURE_OPENAI_ENDPOINT / API_KEY / CHAT_DEPLOYMENT.",
            )
        self._azure: AzureOpenAIConfig = cfg
        self._model = cfg.chat_deployment
        self._max_tokens = max_tokens
        self._url = (
            f"{cfg.endpoint}/openai/deployments/"
            f"{cfg.chat_deployment}/chat/completions"
            f"?api-version={cfg.api_version}"
        )
        request_headers = {
            "api-key": cfg.api_key,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers=request_headers,
        )

    async def __aenter__(self) -> PhotoComparator:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def compare_photos(
        self,
        before_path: str,
        after_path: str,
        *,
        room_context: str | None = None,
    ) -> ComparisonResult:
        """Compare before and after photos to detect property damage.

        Args:
            before_path: Path or URL to the "before" photo.
            after_path: Path or URL to the "after" photo.
            room_context: Optional context about the room (e.g. "master bedroom").

        Returns:
            :class:`ComparisonResult` with identified damages, severity, and costs.
        """
        before_content = self._prepare_image(before_path)
        after_content = self._prepare_image(after_path)

        user_message_parts: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Compare these two photos of a rental property"
                    f"{f' ({room_context})' if room_context else ''}. "
                    f"The first image is BEFORE the guest stay, "
                    f"the second is AFTER checkout."
                ),
            },
            {
                "type": "image_url",
                "image_url": before_content,
            },
            {
                "type": "image_url",
                "image_url": after_content,
            },
        ]

        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": COMPARISON_SYSTEM_PROMPT},
                {"role": "user", "content": user_message_parts},
            ],
            "response_format": {"type": "json_object"},
        }

        raw = await self._call_openai(payload)
        return self._parse_response(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare_image(self, path_or_url: str) -> dict[str, str]:
        """Convert a local path or URL into the format expected by the API."""
        if path_or_url.startswith(("http://", "https://")):
            return {"url": path_or_url}

        file_path = Path(path_or_url)
        if not file_path.exists():
            raise PhotoComparatorError(f"Image file not found: {path_or_url}")

        suffix = file_path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        mime_type = mime_map.get(suffix, "image/jpeg")
        encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        return {"url": f"data:{mime_type};base64,{encoded}"}

    async def _call_openai(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST the chat completion to the tenant's Azure deployment.

        Azure ignores the ``model`` field in the payload — the
        deployment is encoded in the URL.
        """
        try:
            response = await self._client.post(self._url, json=payload)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("Vision API failed: %s", exc.response.text)
            raise PhotoComparatorError(
                f"Vision API error: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Vision API network error: %s", exc)
            raise PhotoComparatorError(
                f"Network error calling Vision API: {exc}"
            ) from exc

    def _parse_response(self, raw: dict[str, Any]) -> ComparisonResult:
        """Parse the GPT-4o JSON response into a ComparisonResult."""
        try:
            choices = raw.get("choices", [])
            if not choices:
                return ComparisonResult(raw_response=raw)

            content = choices[0].get("message", {}).get("content", "{}")
            data: dict[str, Any] = json.loads(content)

            damages = [
                DamageDetail(
                    description=d.get("description", ""),
                    severity=int(d.get("severity", 0)),
                    category=d.get("category", "other"),
                    estimated_cost=float(d.get("estimated_cost", 0)),
                    location=d.get("location", ""),
                )
                for d in data.get("damages", [])
            ]

            total_cost = sum(d.estimated_cost for d in damages)
            no_damage = data.get("no_damage_detected", len(damages) == 0)

            return ComparisonResult(
                damages=damages,
                overall_severity=float(data.get("overall_severity", 0)),
                summary=data.get("summary", ""),
                no_damage_detected=no_damage,
                total_estimated_cost=total_cost,
                raw_response=raw,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error("Failed to parse GPT-4o response: %s", exc)
            return ComparisonResult(
                summary=f"Failed to parse comparison result: {exc}",
                raw_response=raw,
            )

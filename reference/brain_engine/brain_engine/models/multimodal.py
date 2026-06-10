"""Multimodal message support — image, audio, and file content blocks.

Provides typed content block models for multi-modal LLM messages
and utilities for building mixed-content messages.

Example::

    msg = build_multimodal_message(
        text="What's in this image?",
        images=["https://example.com/photo.jpg"],
    )
    response = await model.invoke([msg])

Based on: LangChain multimodal content blocks.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ContentType(StrEnum):
    """Types of content blocks in a multimodal message."""

    TEXT = "text"
    IMAGE_URL = "image_url"
    IMAGE_BASE64 = "image_base64"
    AUDIO_URL = "audio_url"
    FILE_URL = "file_url"


@dataclass(frozen=True, slots=True)
class TextBlock:
    """A plain text content block.

    Attributes:
        text: The text content.
    """

    text: str
    type: str = "text"

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI message format.

        Returns:
            OpenAI content block dict.
        """
        return {"type": "text", "text": self.text}


@dataclass(frozen=True, slots=True)
class ImageBlock:
    """An image content block (URL or base64).

    Attributes:
        url: Image URL or base64 data URI.
        detail: Resolution level (auto, low, high).
    """

    url: str
    detail: str = "auto"
    type: str = "image_url"

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI message format.

        Returns:
            OpenAI image_url content block dict.
        """
        return {
            "type": "image_url",
            "image_url": {
                "url": self.url,
                "detail": self.detail,
            },
        }


@dataclass(frozen=True, slots=True)
class AudioBlock:
    """An audio content block.

    Attributes:
        url: Audio file URL.
        format: Audio format (mp3, wav, etc.).
    """

    url: str
    format: str = "mp3"
    type: str = "audio"

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI message format.

        Returns:
            OpenAI input_audio content block dict.
        """
        return {
            "type": "input_audio",
            "input_audio": {
                "data": self.url,
                "format": self.format,
            },
        }


ContentBlock = TextBlock | ImageBlock | AudioBlock


def build_multimodal_message(
    text: str = "",
    images: list[str] | None = None,
    audio: list[str] | None = None,
    role: str = "user",
    image_detail: str = "auto",
) -> dict[str, Any]:
    """Build a multimodal message from text, images, and audio.

    Args:
        text: Optional text content.
        images: List of image URLs or file paths.
        audio: List of audio URLs.
        role: Message role (user, system, etc.).
        image_detail: Image resolution level.

    Returns:
        Message dict with ``content`` as list of content blocks.
    """
    content: list[dict[str, Any]] = []

    if text:
        content.append(TextBlock(text=text).to_openai())

    for img in (images or []):
        url = _resolve_image_url(img)
        block = ImageBlock(url=url, detail=image_detail)
        content.append(block.to_openai())

    for aud in (audio or []):
        block = AudioBlock(url=aud)
        content.append(block.to_openai())

    return {"role": role, "content": content}


def image_to_base64_url(file_path: str) -> str:
    """Convert a local image file to a base64 data URI.

    Args:
        file_path: Path to the image file.

    Returns:
        Base64 data URI string.

    Raises:
        FileNotFoundError: If the file doesn't exist.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {file_path}")

    mime_type = _detect_mime_type(path)
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _resolve_image_url(source: str) -> str:
    """Resolve an image source to a URL.

    If source starts with http/https, returns as-is.
    If source is a file path, converts to base64 data URI.

    Args:
        source: URL or file path.

    Returns:
        URL string (http or data: URI).
    """
    if source.startswith(("http://", "https://", "data:")):
        return source
    return image_to_base64_url(source)


def _detect_mime_type(path: Path) -> str:
    """Detect MIME type from file extension.

    Args:
        path: File path.

    Returns:
        MIME type string.
    """
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"

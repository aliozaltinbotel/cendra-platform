"""Text splitters — chunk documents for embedding and retrieval.

Provides strategies for splitting text into chunks suitable
for vector embedding: recursive character splitting and
token-based splitting.

Based on: LangChain RecursiveCharacterTextSplitter.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from brain_engine.rag.loaders import Document

logger = logging.getLogger(__name__)

_DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


class TextSplitter(ABC):
    """Abstract base for text splitting strategies."""

    @abstractmethod
    def split_text(self, text: str) -> list[str]:
        """Split text into chunks.

        Args:
            text: Input text.

        Returns:
            List of text chunks.
        """
        ...

    def split_documents(
        self,
        documents: list[Document],
    ) -> list[Document]:
        """Split documents into smaller chunks.

        Preserves metadata from the original document
        and adds chunk index.

        Args:
            documents: Documents to split.

        Returns:
            Chunked documents with inherited metadata.
        """
        chunks: list[Document] = []
        for doc in documents:
            texts = self.split_text(doc.page_content)
            for i, text in enumerate(texts):
                chunk_meta = {
                    **doc.metadata,
                    "chunk_index": i,
                    "total_chunks": len(texts),
                }
                chunks.append(Document(
                    page_content=text,
                    metadata=chunk_meta,
                ))
        return chunks


class RecursiveCharacterSplitter(TextSplitter):
    """Split text recursively by trying separators in order.

    Tries the first separator; if chunks are too large, splits
    each chunk with the next separator, and so on.

    Args:
        chunk_size: Target maximum characters per chunk.
        chunk_overlap: Characters to overlap between chunks.
        separators: Ordered list of separators to try.
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separators: list[str] | None = None,
    ) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._separators = separators or list(_DEFAULT_SEPARATORS)

    def split_text(self, text: str) -> list[str]:
        """Split text using recursive separator strategy.

        Args:
            text: Input text.

        Returns:
            List of text chunks.
        """
        return self._recursive_split(text, self._separators)

    def _recursive_split(
        self,
        text: str,
        separators: list[str],
    ) -> list[str]:
        """Recursively split text with separator fallback.

        Args:
            text: Text to split.
            separators: Remaining separators to try.

        Returns:
            Chunks within size limit.
        """
        if len(text) <= self._chunk_size:
            return [text] if text.strip() else []

        if not separators:
            return _force_split(text, self._chunk_size)

        sep = separators[0]
        remaining_seps = separators[1:]

        if not sep:
            return _force_split(text, self._chunk_size)

        parts = text.split(sep)
        return self._merge_splits(parts, sep, remaining_seps)

    def _merge_splits(
        self,
        parts: list[str],
        separator: str,
        remaining_seps: list[str],
    ) -> list[str]:
        """Merge small parts and recursively split large ones.

        Args:
            parts: Text parts from splitting.
            separator: The separator that was used.
            remaining_seps: Separators for further splitting.

        Returns:
            Final chunks.
        """
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for part in parts:
            part_len = len(part) + len(separator)
            if current_len + part_len > self._chunk_size and current:
                chunk_text = separator.join(current)
                chunks.append(chunk_text)
                current = _compute_overlap(
                    current, separator, self._chunk_overlap,
                )
                current_len = sum(
                    len(c) + len(separator) for c in current
                )
            current.append(part)
            current_len += part_len

        if current:
            chunk_text = separator.join(current)
            if len(chunk_text) > self._chunk_size and remaining_seps:
                chunks.extend(
                    self._recursive_split(chunk_text, remaining_seps),
                )
            elif chunk_text.strip():
                chunks.append(chunk_text)

        return chunks


class TokenSplitter(TextSplitter):
    """Split text by approximate token count.

    Uses word-based approximation (~1.3 tokens per word).

    Args:
        chunk_size: Target tokens per chunk.
        chunk_overlap: Tokens to overlap.
    """

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> list[str]:
        """Split text by token count estimation.

        Args:
            text: Input text.

        Returns:
            List of token-bounded chunks.
        """
        words = text.split()
        words_per_chunk = int(self._chunk_size / 1.3)
        overlap_words = int(self._chunk_overlap / 1.3)

        if len(words) <= words_per_chunk:
            return [text] if text.strip() else []

        return _split_by_words(words, words_per_chunk, overlap_words)


# ── Helpers ──────────────────────────────────────────────────────────── #


def _force_split(text: str, size: int) -> list[str]:
    """Force-split text at exact character boundaries.

    Args:
        text: Text to split.
        size: Maximum chunk size.

    Returns:
        Chunks of at most ``size`` characters.
    """
    return [
        text[i:i + size]
        for i in range(0, len(text), size)
        if text[i:i + size].strip()
    ]


def _compute_overlap(
    parts: list[str],
    separator: str,
    overlap: int,
) -> list[str]:
    """Keep trailing parts for overlap.

    Args:
        parts: Current parts list.
        separator: Separator string.
        overlap: Desired overlap in characters.

    Returns:
        Parts to carry over for overlap.
    """
    if overlap <= 0:
        return []
    carry: list[str] = []
    total = 0
    for part in reversed(parts):
        total += len(part) + len(separator)
        if total > overlap:
            break
        carry.insert(0, part)
    return carry


def _split_by_words(
    words: list[str],
    chunk_words: int,
    overlap_words: int,
) -> list[str]:
    """Split word list into overlapping chunks.

    Args:
        words: All words.
        chunk_words: Words per chunk.
        overlap_words: Overlap in words.

    Returns:
        Joined text chunks.
    """
    step = max(1, chunk_words - overlap_words)
    chunks: list[str] = []
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + chunk_words])
        if chunk.strip():
            chunks.append(chunk)
    return chunks

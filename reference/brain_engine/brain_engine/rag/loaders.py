"""Document loaders — load documents from various sources.

Provides typed document loading from text files, JSON, Markdown,
and other formats. Each loader produces ``Document`` objects with
content and metadata.

Based on: LangChain DocumentLoader pattern.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Document:
    """A loaded document with content and metadata.

    Attributes:
        page_content: The text content of the document.
        metadata: Key-value metadata (source, title, etc.).
        doc_id: Optional unique identifier.
    """

    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    doc_id: str = ""


class DocumentLoader(ABC):
    """Abstract base for document loaders."""

    @abstractmethod
    async def load(self) -> list[Document]:
        """Load documents from the source.

        Returns:
            List of Document objects.
        """
        ...

    async def lazy_load(self) -> list[Document]:
        """Load documents lazily (default: same as load).

        Returns:
            List of Document objects.
        """
        return await self.load()


class TextFileLoader(DocumentLoader):
    """Load plain text files as documents.

    Args:
        file_path: Path to the text file.
        encoding: File encoding.
    """

    def __init__(
        self,
        file_path: str,
        encoding: str = "utf-8",
    ) -> None:
        self._path = Path(file_path)
        self._encoding = encoding

    async def load(self) -> list[Document]:
        """Load the text file as a single document.

        Returns:
            List with one Document.

        Raises:
            FileNotFoundError: If file doesn't exist.
        """
        if not self._path.is_file():
            raise FileNotFoundError(f"Not found: {self._path}")
        content = self._path.read_text(encoding=self._encoding)
        return [Document(
            page_content=content,
            metadata={
                "source": str(self._path),
                "file_name": self._path.name,
                "file_type": self._path.suffix,
            },
        )]


class JSONLoader(DocumentLoader):
    """Load JSON files, extracting text from specified fields.

    Args:
        file_path: Path to JSON file.
        content_key: Key containing the text content.
        metadata_keys: Keys to include as metadata.
        is_array: Whether the JSON root is an array.
    """

    def __init__(
        self,
        file_path: str,
        content_key: str = "content",
        metadata_keys: list[str] | None = None,
        is_array: bool = True,
    ) -> None:
        self._path = Path(file_path)
        self._content_key = content_key
        self._metadata_keys = metadata_keys or []
        self._is_array = is_array

    async def load(self) -> list[Document]:
        """Load JSON entries as documents.

        Returns:
            List of Documents, one per JSON object.
        """
        raw = self._path.read_text(encoding="utf-8")
        data = json.loads(raw)
        items = data if self._is_array else [data]
        return [self._to_document(item) for item in items]

    def _to_document(self, item: dict[str, Any]) -> Document:
        """Convert a JSON object to a Document.

        Args:
            item: JSON dict.

        Returns:
            Document with extracted content and metadata.
        """
        content = str(item.get(self._content_key, ""))
        metadata = {"source": str(self._path)}
        for key in self._metadata_keys:
            if key in item:
                metadata[key] = item[key]
        return Document(page_content=content, metadata=metadata)


class MarkdownLoader(DocumentLoader):
    """Load Markdown files with section-based splitting.

    Each heading (``#``, ``##``, etc.) creates a new document
    with the heading as metadata.

    Args:
        file_path: Path to Markdown file.
        split_on_headings: Whether to split on headings.
    """

    def __init__(
        self,
        file_path: str,
        split_on_headings: bool = True,
    ) -> None:
        self._path = Path(file_path)
        self._split = split_on_headings

    async def load(self) -> list[Document]:
        """Load Markdown file as documents.

        Returns:
            List of Documents (one per section or one total).
        """
        content = self._path.read_text(encoding="utf-8")
        if not self._split:
            return [Document(
                page_content=content,
                metadata={"source": str(self._path)},
            )]
        return _split_markdown_sections(content, str(self._path))


class DirectoryLoader(DocumentLoader):
    """Load all supported files from a directory.

    Args:
        directory: Directory path.
        glob_pattern: File pattern to match.
        recursive: Whether to search subdirectories.
    """

    def __init__(
        self,
        directory: str,
        glob_pattern: str = "**/*.*",
        recursive: bool = True,
    ) -> None:
        self._dir = Path(directory)
        self._pattern = glob_pattern
        self._recursive = recursive

    async def load(self) -> list[Document]:
        """Load all matching files as documents.

        Returns:
            List of Documents from all matched files.
        """
        documents: list[Document] = []
        for file_path in self._dir.glob(self._pattern):
            if not file_path.is_file():
                continue
            loader = _get_loader_for_file(file_path)
            if loader:
                docs = await loader.load()
                documents.extend(docs)
        return documents


# ── Helpers ──────────────────────────────────────────────────────────── #


def _split_markdown_sections(
    content: str,
    source: str,
) -> list[Document]:
    """Split markdown by headings into separate documents.

    Args:
        content: Full markdown text.
        source: Source file path.

    Returns:
        List of Documents per section.
    """
    sections: list[Document] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("#"):
            if current_lines:
                sections.append(_make_section_doc(
                    current_heading, current_lines, source,
                ))
            current_heading = line.strip("# ").strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append(_make_section_doc(
            current_heading, current_lines, source,
        ))

    return sections


def _make_section_doc(
    heading: str,
    lines: list[str],
    source: str,
) -> Document:
    """Create a Document from a markdown section.

    Args:
        heading: Section heading.
        lines: Content lines.
        source: Source file.

    Returns:
        Document with section content.
    """
    return Document(
        page_content="\n".join(lines).strip(),
        metadata={"source": source, "heading": heading},
    )


def _get_loader_for_file(path: Path) -> DocumentLoader | None:
    """Get appropriate loader for a file type.

    Args:
        path: File path.

    Returns:
        Loader instance or None if unsupported.
    """
    suffix = path.suffix.lower()
    if suffix in (".txt", ".log", ".cfg", ".ini"):
        return TextFileLoader(str(path))
    if suffix == ".json":
        return JSONLoader(str(path))
    if suffix in (".md", ".markdown"):
        return MarkdownLoader(str(path))
    return None

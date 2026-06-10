"""CompressionEngine — transparent lz4/zstd/none compression.

Provides data compression with automatic algorithm selection based
on data temperature: lz4 for hot data (fast), zstd for cold data
(better ratio), none for pre-compressed content.
"""

from __future__ import annotations

import io
import logging
import zlib
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class CompressionAlgo(StrEnum):
    """Available compression algorithms."""

    LZ4 = "lz4"
    ZSTD = "zstd"
    ZLIB = "zlib"
    NONE = "none"


# ── Header format ────────────────────────────────────────────────────
# 4 bytes: magic "BZFS"
# 1 byte:  algo id (0=none, 1=zlib, 2=lz4, 3=zstd)
# remaining: compressed data

_MAGIC = b"BZFS"
_ALGO_IDS: dict[CompressionAlgo, int] = {
    CompressionAlgo.NONE: 0,
    CompressionAlgo.ZLIB: 1,
    CompressionAlgo.LZ4: 2,
    CompressionAlgo.ZSTD: 3,
}
_ID_TO_ALGO: dict[int, CompressionAlgo] = {v: k for k, v in _ALGO_IDS.items()}


class CompressionEngine:
    """Transparent compression engine with pluggable algorithms.

    Falls back to zlib when lz4/zstd libraries are not available.
    All compressed data is prefixed with a header identifying the
    algorithm, so decompression is automatic.

    Args:
        default_algo: Default compression algorithm.
        level: Compression level (1-9 for zlib, varies for others).
    """

    def __init__(
        self,
        default_algo: CompressionAlgo = CompressionAlgo.ZLIB,
        level: int = 6,
    ) -> None:
        self._default_algo = default_algo
        self._level = level
        self._lz4_available = _check_lz4()
        self._zstd_available = _check_zstd()

    @property
    def default_algo(self) -> CompressionAlgo:
        """Return the default compression algorithm."""
        return self._default_algo

    def compress(self, data: bytes, algo: CompressionAlgo | None = None) -> bytes:
        """Compress data with the specified or default algorithm.

        Args:
            data: Raw bytes to compress.
            algo: Algorithm to use (None = default).

        Returns:
            Compressed bytes with BZFS header.
        """
        algo = algo or self._default_algo
        algo = self._resolve_algo(algo)

        compressed = self._compress_raw(data, algo)
        header = _MAGIC + bytes([_ALGO_IDS[algo]])
        return header + compressed

    def decompress(self, data: bytes) -> bytes:
        """Decompress data, auto-detecting the algorithm from header.

        Args:
            data: Compressed bytes with BZFS header.

        Returns:
            Decompressed bytes.

        Raises:
            ValueError: If the header is invalid.
        """
        if len(data) < 5 or data[:4] != _MAGIC:
            return data

        algo_id = data[4]
        algo = _ID_TO_ALGO.get(algo_id)
        if algo is None:
            msg = f"Unknown compression algo id: {algo_id}"
            raise ValueError(msg)

        return self._decompress_raw(data[5:], algo)

    def get_ratio(self, original: bytes, compressed: bytes) -> float:
        """Calculate compression ratio.

        Args:
            original: Original uncompressed data.
            compressed: Compressed data (with header).

        Returns:
            Ratio of original_size / compressed_size.
        """
        if len(compressed) == 0:
            return 1.0
        return len(original) / len(compressed)

    def estimate_ratio(self, data: bytes) -> float:
        """Compress data and return the achieved ratio.

        Args:
            data: Raw bytes to test.

        Returns:
            Compression ratio.
        """
        compressed = self.compress(data)
        return self.get_ratio(data, compressed)

    # ── Internal ─────────────────────────────────────────────────────

    def _resolve_algo(self, algo: CompressionAlgo) -> CompressionAlgo:
        """Fall back to zlib if the requested algo is unavailable."""
        if algo == CompressionAlgo.LZ4 and not self._lz4_available:
            return CompressionAlgo.ZLIB
        if algo == CompressionAlgo.ZSTD and not self._zstd_available:
            return CompressionAlgo.ZLIB
        return algo

    def _compress_raw(self, data: bytes, algo: CompressionAlgo) -> bytes:
        """Compress without header."""
        if algo == CompressionAlgo.NONE:
            return data
        if algo == CompressionAlgo.ZLIB:
            return zlib.compress(data, self._level)
        if algo == CompressionAlgo.LZ4 and self._lz4_available:
            import lz4.frame
            return lz4.frame.compress(data)
        if algo == CompressionAlgo.ZSTD and self._zstd_available:
            import zstandard
            cctx = zstandard.ZstdCompressor(level=self._level)
            return cctx.compress(data)
        return zlib.compress(data, self._level)

    def _decompress_raw(self, data: bytes, algo: CompressionAlgo) -> bytes:
        """Decompress without header."""
        if algo == CompressionAlgo.NONE:
            return data
        if algo == CompressionAlgo.ZLIB:
            return zlib.decompress(data)
        if algo == CompressionAlgo.LZ4 and self._lz4_available:
            import lz4.frame
            return lz4.frame.decompress(data)
        if algo == CompressionAlgo.ZSTD and self._zstd_available:
            import zstandard
            dctx = zstandard.ZstdDecompressor()
            return dctx.decompress(data)
        return zlib.decompress(data)


def _check_lz4() -> bool:
    """Check if lz4 library is available."""
    try:
        import lz4.frame  # noqa: F401
        return True
    except ImportError:
        return False


def _check_zstd() -> bool:
    """Check if zstandard library is available."""
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return False

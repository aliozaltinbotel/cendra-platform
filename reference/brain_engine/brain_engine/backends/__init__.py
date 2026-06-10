"""Backends module — pluggable storage abstraction."""

from brain_engine.backends.protocol import BackendProtocol
from brain_engine.backends.filesystem import FilesystemBackend
from brain_engine.backends.state import StateBackend
from brain_engine.backends.composite import CompositeBackend

__all__ = [
    "BackendProtocol",
    "FilesystemBackend",
    "StateBackend",
    "CompositeBackend",
]

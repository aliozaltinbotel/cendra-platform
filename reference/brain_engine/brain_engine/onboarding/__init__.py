"""Onboarding package.

Bundles two independent subsystems:

- **Template store** — bulk rule provisioning on property creation.
- **Historical bootstrap** — replays archived PMS conversations through
  the learning pipeline so a freshly-provisioned property has warm
  DecisionCase / PatternRule state from day one.

The bootstrap subsystem is currently wired up in parts; see
``conversation_archive``, ``models``, and ``errors``.
"""

from __future__ import annotations

from brain_engine.onboarding.conversation_archive import (
    ConversationArchiveLoader,
)
from brain_engine.onboarding.bootstrap_pipeline import (
    BootstrapJobState,
    BootstrapPropertyReport,
    BootstrapReport,
    BootstrapRequest,
    OnboardingBootstrapPipeline,
)
from brain_engine.onboarding.episode_builder import (
    DEFAULT_MAX_GAP_HOURS,
    EpisodeBuilder,
    EpisodeStats,
)
from brain_engine.onboarding.errors import (
    ConversationArchiveError,
    HistoricalExtractionError,
    OnboardingError,
)
from brain_engine.onboarding.graphql_archive_loader import (
    GraphQLConversationArchiveLoader,
)
from brain_engine.onboarding.historical_case_extractor import (
    HistoricalCaseExtractor,
)
from brain_engine.onboarding.models import (
    ArchivedConversation,
    ArchivedMessage,
    MessageSender,
    OnboardingReport,
    OnboardingRequest,
    PropertyReport,
)
from brain_engine.onboarding.service import OnboardingService
from brain_engine.onboarding.template_store import TemplateStore

__all__ = [
    "ArchivedConversation",
    "ArchivedMessage",
    "BootstrapJobState",
    "BootstrapPropertyReport",
    "BootstrapReport",
    "BootstrapRequest",
    "ConversationArchiveError",
    "ConversationArchiveLoader",
    "DEFAULT_MAX_GAP_HOURS",
    "EpisodeBuilder",
    "EpisodeStats",
    "GraphQLConversationArchiveLoader",
    "HistoricalCaseExtractor",
    "HistoricalExtractionError",
    "MessageSender",
    "OnboardingBootstrapPipeline",
    "OnboardingError",
    "OnboardingReport",
    "OnboardingRequest",
    "OnboardingService",
    "PropertyReport",
    "TemplateStore",
]

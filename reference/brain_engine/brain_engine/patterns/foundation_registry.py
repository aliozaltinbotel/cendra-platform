"""Foundation document parser — turns the markdown into ScenarioExamples.

The Brain Engine hospitality foundation
(``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md``)
enumerates 469 hospitality scenarios as numbered ``## N. Title``
blocks with structured sub-sections.  Each scenario carries fifteen
sub-section fields, captured as a single :class:`FoundationScenario`
dataclass:

* ``### Stage`` — booking-journey stage label *(redundant with the
  parent ``# Stage N — Label`` header; intentionally not stored)*
* ``### Trigger`` — canonical free-form description of when the
  scenario fires (the input to the embedding matcher)
* ``### Risk Level`` — ``Low | Medium | High | Critical``
* ``### Signals to Inspect`` — bullet list of data the classifier
  should consult
* ``### AI Default Behavior`` — guidance prose for the agent
* ``### Required Data Checks`` — bullet list of integration reads
* Four ``Should AI …`` decision flags — ``Yes | No | Conditional``
  (``Learn Pattern?`` is only ``Yes | No``)
* ``### Pattern to Learn`` — what pattern miners should extract
* ``### Example Learned Pattern`` — illustrative example
* ``### Memory Type`` — bullet list of memory tiers the case routes
  into (one scenario may fan out to multiple tiers)
* ``### What Not to Learn`` — safety guardrails the learner must
  respect
* ``### Future Behavior Impact`` — expected downstream behaviour

The minimal subset (``scenario_id``, ``title``, ``stage_number``,
``stage_label``, ``trigger``) is enough to seed the
:class:`~brain_engine.patterns.scenario_matcher.ScenarioMatcher`
embedding index — :func:`load_foundation_examples` keeps producing
those :class:`ScenarioExample` rows for backward compatibility.

The full 14-field projection is used by Foundation Layer code paths
that need memory routing, safety gating, and pattern-mining
guardrails — Sprint 2+ of the Foundation Layer roadmap.

Honest scope
------------

* Pure-Python regex parser.  No markdown library dependency.
* The parser is *forgiving*: a scenario missing a sub-section keeps
  the corresponding field at its default empty value rather than
  raising.  A scenario missing ``### Trigger`` is logged at WARNING
  by :func:`load_foundation_examples` and dropped from the matcher
  index (since an empty trigger has nothing to embed).
* Slug generation is deterministic — ``"5. Same-night inquiry
  after 22:00 …"`` becomes ``"s1_5_same_night_inquiry_after_2200"``
  (stage_prefix + scenario_number + first 6 title words).  The
  prefix guarantees uniqueness across the 469 entries.

References
----------
* Hospitality Foundation MD: 469 scenarios, 9 stages.  See repo
  root ``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_
  Foundation.md`` Section 9 *Scenario Catalog* for the field-level
  schema this parser pins.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import structlog

from brain_engine.patterns.scenario_matcher import ScenarioExample

__all__ = [
    "FoundationScenario",
    "compute_doc_hash",
    "load_foundation_examples",
    "load_foundation_scenarios",
    "parse_foundation_document",
]


logger = structlog.get_logger(__name__)


# ── parser regex tokens ───────────────────────────────────── #


_STAGE_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^#\s+Stage\s+(\d+)\s+—\s+(.+?)\s*$",
)
_SCENARIO_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^##\s+(\d+)\.\s+(.+?)\s*$",
)
_SUBSECTION_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^###\s+(.+?)\s*$",
)
_BULLET_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*[-*]\s+(.+?)\s*$",
)
_SLUG_TITLE_WORDS: Final[int] = 6


# ── value object ──────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class FoundationScenario:
    """One parsed scenario from the foundation document.

    Captures the full 14 sub-section fields each ``## N. Title``
    block carries, plus the four header-derived attributes
    (``scenario_id``, ``title``, ``stage_number``, ``stage_label``)
    and the canonicalised ``trigger`` body.  The inner ``### Stage``
    sub-section is intentionally not stored — it duplicates the
    parent ``# Stage N — Label`` header and can drift out of sync
    with the canonical stage label.

    All FL-01 additions default to safe empty values so callers
    that pre-date the expansion (notably the existing matcher
    fixtures and the historical Sprint H/I tests) keep constructing
    instances with the original five-field set.

    Attributes:
        scenario_id: Deterministic slug derived from the stage
            number, scenario index, and first words of the title.
        title: Verbatim scenario title (the text after ``## N.``).
        stage_number: 1—9 per the foundation's 9-stage ladder.
        stage_label: Textual label after the em-dash in the stage
            header (e.g. ``"Pre-Booking / Inquiry"``).
        trigger: ``### Trigger`` paragraph body, joined with ``\\n``
            and stripped.  Empty when the section is missing.
        risk_level: ``### Risk Level`` — one of ``Low | Medium |
            High | Critical``.  Empty string when missing.
        ai_default_behavior: ``### AI Default Behavior`` text.
        required_data_checks: Bullet items under ``### Required
            Data Checks`` — empty tuple when missing.
        signals_to_inspect: Bullet items under ``### Signals to
            Inspect``.
        should_auto_reply: ``Yes | No | Conditional`` — the
            scenario's auto-reply policy.
        should_escalate_to_pm: ``Yes | No | Conditional``.
        should_create_task: ``Yes | No | Conditional``.
        should_learn_pattern: ``Yes | No`` (the foundation never
            uses ``Conditional`` for the learn-pattern flag).
        pattern_to_learn: Free-text description of the pattern the
            learning subsystem should extract.
        example_learned_pattern: Illustrative example for that
            pattern.
        memory_types: Bullet items under ``### Memory Type`` —
            a scenario may fan out to multiple memory tiers.
        what_not_to_learn: Free-text safety guardrails.
        future_behavior_impact: Free-text expected downstream
            impact.

    Raises:
        ValueError: If ``scenario_id`` is empty or ``stage_number``
            falls outside ``[1, 9]``.
    """

    scenario_id: str
    title: str
    stage_number: int
    stage_label: str
    trigger: str
    risk_level: str = ""
    ai_default_behavior: str = ""
    required_data_checks: tuple[str, ...] = ()
    signals_to_inspect: tuple[str, ...] = ()
    should_auto_reply: str = ""
    should_escalate_to_pm: str = ""
    should_create_task: str = ""
    should_learn_pattern: str = ""
    pattern_to_learn: str = ""
    example_learned_pattern: str = ""
    memory_types: tuple[str, ...] = ()
    what_not_to_learn: str = ""
    future_behavior_impact: str = ""

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id required")
        if not 1 <= self.stage_number <= 9:
            raise ValueError(
                "stage_number must be in [1, 9]",
            )


# ── helpers — slug + body extraction ──────────────────────── #


def _slugify(words: Iterable[str]) -> str:
    """Lowercase + underscore the supplied words (ASCII-safe)."""
    parts: list[str] = []
    for word in words:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "", word).lower()
        if cleaned:
            parts.append(cleaned)
    return "_".join(parts)


def _build_id(
    *,
    stage_number: int,
    scenario_number: int,
    title: str,
) -> str:
    """Build the canonical slug used as ``scenario_id``."""
    words = title.split()[:_SLUG_TITLE_WORDS]
    slug = _slugify(words)
    return f"s{stage_number}_{scenario_number}_{slug}"


def _collect_text(lines: Iterable[str]) -> str:
    """Join sub-section body lines with ``\\n`` and strip the result."""
    return "\n".join(lines).strip()


def _collect_bullets(lines: Iterable[str]) -> tuple[str, ...]:
    """Extract ``- item`` lines from a sub-section body.

    Non-bullet lines are ignored — many sub-sections mix a short
    introductory paragraph with the bullet list.  Empty bullets
    (trailing dashes with no payload) are also skipped.
    """
    items: list[str] = []
    for line in lines:
        match = _BULLET_LINE_RE.match(line)
        if match is None:
            continue
        item = match.group(1).strip()
        if item:
            items.append(item)
    return tuple(items)


# Type alias for the per-subsection collector callables.  Each
# collector takes the raw line buffer and returns the typed value
# (``str`` for prose, ``tuple[str, ...]`` for bullet lists).
_Collector = Callable[[Iterable[str]], object]


# Maps the lowercased ``### Heading`` text to ``(field_name,
# collector)``.  Keeping the table in one place makes adding a new
# sub-section a one-line change (plus the dataclass field).
_SUBSECTION_FIELDS: Final[Mapping[str, tuple[str, _Collector]]] = {
    "trigger": ("trigger", _collect_text),
    "risk level": ("risk_level", _collect_text),
    "ai default behavior": ("ai_default_behavior", _collect_text),
    "required data checks": (
        "required_data_checks",
        _collect_bullets,
    ),
    "signals to inspect": (
        "signals_to_inspect",
        _collect_bullets,
    ),
    "should ai auto-reply?": (
        "should_auto_reply",
        _collect_text,
    ),
    "should ai escalate to pm?": (
        "should_escalate_to_pm",
        _collect_text,
    ),
    "should ai create task?": (
        "should_create_task",
        _collect_text,
    ),
    "should ai learn pattern?": (
        "should_learn_pattern",
        _collect_text,
    ),
    "pattern to learn": ("pattern_to_learn", _collect_text),
    "example learned pattern": (
        "example_learned_pattern",
        _collect_text,
    ),
    "memory type": ("memory_types", _collect_bullets),
    "what not to learn": ("what_not_to_learn", _collect_text),
    "future behavior impact": (
        "future_behavior_impact",
        _collect_text,
    ),
}


# ── parser core ───────────────────────────────────────────── #


@dataclass(slots=True)
class _OpenScenario:
    """Mutable accumulator for an in-progress scenario block.

    The walker fills this object as it iterates the markdown stream;
    :func:`_finalise` converts it into an immutable
    :class:`FoundationScenario` once the block closes (next ``## N.``
    header or next ``# Stage`` header).
    """

    stage_number: int
    stage_label: str
    scenario_number: int
    title: str
    subsections: dict[str, list[str]] = field(default_factory=dict)
    current_subsection: str | None = None

    def open_subsection(self, name: str) -> None:
        """Begin buffering body lines under ``name`` (lowercased)."""
        self.current_subsection = name
        # Pre-create the list so a section opened but immediately
        # closed by the next ``###`` still records an empty bucket
        # — this matters for *missing-content* vs *missing-section*
        # disambiguation downstream.
        self.subsections.setdefault(name, [])

    def append_line(self, line: str) -> None:
        """Append a body line to the current sub-section buffer.

        Lines outside any ``###`` heading are dropped — the
        foundation document never carries scenario-level prose
        between sub-sections, so silently ignoring them is safe and
        keeps the parser tight.
        """
        if self.current_subsection is None:
            return
        self.subsections[self.current_subsection].append(line)


def _finalise(open_scenario: _OpenScenario) -> FoundationScenario:
    """Build an immutable :class:`FoundationScenario` from the buffer."""
    scenario_id = _build_id(
        stage_number=open_scenario.stage_number,
        scenario_number=open_scenario.scenario_number,
        title=open_scenario.title,
    )

    kwargs: dict[str, object] = {}
    for heading, (field_name, collector) in _SUBSECTION_FIELDS.items():
        body = open_scenario.subsections.get(heading)
        if body is None:
            continue
        kwargs[field_name] = collector(body)

    return FoundationScenario(
        scenario_id=scenario_id,
        title=open_scenario.title,
        stage_number=open_scenario.stage_number,
        stage_label=open_scenario.stage_label,
        # ``trigger`` is part of the sub-section table; default the
        # positional argument here so the dataclass call succeeds
        # even when the document omitted the section.  ``kwargs``
        # below overrides this with the parsed value when present.
        trigger=str(kwargs.pop("trigger", "")),
        **kwargs,  # type: ignore[arg-type]
    )


def parse_foundation_document(
    markdown: str,
) -> tuple[FoundationScenario, ...]:
    """Walk ``markdown`` line-by-line and emit one scenario per ``## N.``.

    The walker keeps two pieces of state:

    * ``current_stage`` — the most recent ``# Stage N — …`` header
      seen.  Scenarios that appear before any stage header are
      ignored, mirroring the document's structure.
    * ``open_scenario`` — the in-progress :class:`_OpenScenario`
      buffer.  When the next ``## N.`` header (or ``# Stage`` header)
      arrives, the buffer is finalised and a
      :class:`FoundationScenario` is appended to the output.

    The function is pure: no I/O, no logging side effects.  The
    file-loading wrappers :func:`load_foundation_examples` and
    :func:`load_foundation_scenarios` add those.
    """
    scenarios: list[FoundationScenario] = []
    current_stage_number: int | None = None
    current_stage_label = ""
    open_scenario: _OpenScenario | None = None

    def flush() -> None:
        nonlocal open_scenario
        if open_scenario is None:
            return
        scenarios.append(_finalise(open_scenario))
        open_scenario = None

    for raw in markdown.splitlines():
        line = raw.rstrip("\r")

        stage_match = _STAGE_HEADER_RE.match(line)
        if stage_match:
            flush()
            current_stage_number = int(stage_match.group(1))
            current_stage_label = stage_match.group(2)
            continue

        scenario_match = _SCENARIO_HEADER_RE.match(line)
        if scenario_match and current_stage_number is not None:
            flush()
            open_scenario = _OpenScenario(
                stage_number=current_stage_number,
                stage_label=current_stage_label,
                scenario_number=int(scenario_match.group(1)),
                title=scenario_match.group(2),
            )
            continue

        if open_scenario is None:
            continue

        sub_match = _SUBSECTION_HEADER_RE.match(line)
        if sub_match:
            heading = sub_match.group(1).strip().lower()
            open_scenario.open_subsection(heading)
            continue

        if line.strip():
            open_scenario.append_line(line.strip())

    flush()
    return tuple(scenarios)


# ── loaders ───────────────────────────────────────────────── #


# Parse cache keyed by the SHA-256 of the document text.  Parsing the
# 470+ scenario markdown is regex-heavy; several components load the
# same shipped file independently at startup, so without this they each
# re-parse it (observed: 6 parses in 2s on a cold pod).  Keying by
# content digest — not path — means a re-parse happens iff the bytes
# actually change, so tests that rewrite a temp file still see fresh
# data (new content → new digest → reparse).  Cached values are tuples
# of frozen ``FoundationScenario`` dataclasses, safe to share.
_SCENARIO_PARSE_CACHE: dict[str, tuple[FoundationScenario, ...]] = {}


def load_foundation_scenarios(
    markdown_path: Path | str,
) -> tuple[FoundationScenario, ...]:
    """Read ``markdown_path`` and parse all scenarios.

    Returns the full :class:`FoundationScenario` tuple (all 14
    sub-section fields populated when present in the document).
    Scenarios with empty triggers are kept — Foundation Layer
    consumers that need a non-empty trigger should filter
    downstream (the matcher loader :func:`load_foundation_examples`
    already does so).

    The parse is memoised by document content digest, so repeated loads
    of the same (unchanged) file are served from cache instead of
    re-parsing.

    A missing file is logged at WARNING and returns an empty tuple
    so callers can degrade gracefully without try/except boilerplate.
    """
    path = Path(markdown_path)
    if not path.is_file():
        logger.warning(
            "foundation_registry.missing_file",
            path=str(path),
        )
        return ()
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cached = _SCENARIO_PARSE_CACHE.get(digest)
    if cached is not None:
        logger.debug(
            "foundation_registry.loaded_cached",
            path=str(path),
            scenarios=len(cached),
        )
        return cached
    parsed = parse_foundation_document(text)
    _SCENARIO_PARSE_CACHE[digest] = parsed
    logger.info(
        "foundation_registry.loaded_full",
        path=str(path),
        scenarios=len(parsed),
    )
    return parsed


def load_foundation_examples(
    markdown_path: Path | str,
) -> tuple[ScenarioExample, ...]:
    """Load the foundation document into ``ScenarioExample`` rows.

    Scenarios missing the ``### Trigger`` body are dropped with a
    WARNING log entry so a corrupt section does not poison the
    matcher's index.  Returns an empty tuple when the file does not
    exist — callers can decide whether to error out or fall back to
    a smaller hand-curated registry.

    This loader is the backward-compatible entry point that pre-dates
    the FL-01 expansion; new code that needs the full 14-field
    payload should call :func:`load_foundation_scenarios` instead.
    """
    parsed = load_foundation_scenarios(markdown_path)
    examples: list[ScenarioExample] = []
    missing_trigger = 0
    for row in parsed:
        if not row.trigger:
            missing_trigger += 1
            continue
        examples.append(
            ScenarioExample(
                scenario_id=row.scenario_id,
                text=row.trigger,
            ),
        )
    if missing_trigger:
        logger.warning(
            "foundation_registry.missing_trigger",
            count=missing_trigger,
        )
    logger.info(
        "foundation_registry.loaded_examples",
        scenarios=len(examples),
    )
    return tuple(examples)


def compute_doc_hash(markdown_path: Path | str) -> str | None:
    """Return the SHA-256 hex digest of the foundation document.

    Used by :class:`brain_engine.patterns.foundation_catalog_store.
    FoundationCatalogStore` implementations to detect when the
    shipped MD has changed and a re-parse + upsert is needed.
    Returns ``None`` when the file is missing — same fall-through
    contract as the other loaders.
    """
    path = Path(markdown_path)
    if not path.is_file():
        logger.warning(
            "foundation_registry.hash_missing_file",
            path=str(path),
        )
        return None
    digest = hashlib.sha256(
        path.read_bytes(),
    ).hexdigest()
    return digest

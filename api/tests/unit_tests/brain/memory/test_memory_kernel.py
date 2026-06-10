"""Focused coverage for the Batch 3 memory kernel.

Written at port time for the modules whose reference tests live in
Batch 6 wrappers (nightly consolidator) or did not exist: working /
episodic tiers, dedup primitives, surprise gating, and the
llm_generator-seamed fact extraction that replaced mem0ai.
"""

from __future__ import annotations

import pytest

from core.brain.memory.episodic_dedup import cosine_similarity
from core.brain.memory.episodic_memory import Episode, EpisodicMemory, JsonFileBackend
from core.brain.memory.fact_extraction import (
    ExtractedFact,
    LLMFactExtractor,
    NullFactExtractor,
)
from core.brain.memory.working_memory import WorkingMemory


class TestWorkingMemory:
    def test_truncation_preserves_system_turns(self):
        wm = WorkingMemory(max_turns=3)
        wm.add_turn("system", "you are cendra")
        for i in range(5):
            wm.add_turn("user", f"msg {i}")
        turns = wm.get_turns()
        assert len(turns) == 3
        assert turns[0].role == "system"
        assert turns[-1].content == "msg 4"

    def test_messages_shape_and_last_accessors(self):
        wm = WorkingMemory()
        wm.add_turn("user", "hello")
        wm.add_turn("assistant", "hi there")
        assert wm.get_messages() == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        assert wm.last_user_message == "hello"
        assert wm.last_assistant_message == "hi there"

    def test_reset_clears_everything(self):
        wm = WorkingMemory()
        wm.add_turn("user", "x")
        wm.set_context("k", 1)
        wm.set_scratch("s", 2)
        wm.reset()
        assert wm.turn_count == 0
        assert wm.get_context("k") is None
        assert wm.get_scratch("s") is None


class TestEpisodicMemory:
    def test_json_backend_round_trip_and_recency(self, tmp_path):
        backend = JsonFileBackend(tmp_path / "episodes.json")
        session_one = EpisodicMemory(backend=backend, session_id="s1")
        session_two = EpisodicMemory(backend=backend, session_id="s2")
        session_one.add_episode("checkin", "guest arrived")
        session_one.add_episode("complaint", "noise next door")
        session_two.add_episode("checkout", "guest left")
        # get_recent is scoped to the wrapper's session
        recent = session_one.get_recent(2)
        assert [e.event for e in recent] == ["checkin", "complaint"]
        assert [e.event for e in session_two.get_recent(5)] == ["checkout"]
        assert [e.event for e in session_one.get_session_history("s1")] == [
            "checkin",
            "complaint",
        ]

    def test_episode_serialisation_round_trip(self):
        episode = Episode(event="checkin", content="x", session_id="s1")
        rebuilt = Episode.from_dict(episode.to_dict())
        assert rebuilt == episode


class TestDedupPrimitives:
    def test_cosine_similarity_bounds(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_cosine_similarity_length_mismatch(self):
        with pytest.raises(ValueError, match="length mismatch"):
            cosine_similarity([1.0], [1.0, 2.0])


class TestFactExtraction:
    def test_null_extractor_degrades(self):
        extractor = NullFactExtractor()
        assert extractor.is_available() is False
        assert extractor.extract_facts([{"role": "user", "content": "hi"}]) == []

    def test_llm_extractor_parses_and_validates(self):
        response = """```json
        [{"content": "Guest prefers late checkout", "fact_type": "preference",
          "confidence": 0.9, "keywords": ["checkout"]},
         {"content": "Pool closes at 22:00", "fact_type": "weird_type"},
         {"content": "", "fact_type": "info"},
         "not-a-dict"]
        ```"""
        extractor = LLMFactExtractor(lambda prompt: response)
        facts = extractor.extract_facts(
            [{"role": "user", "content": "can we leave late?"}],
            entity_id="guest:1",
            source="ep-9",
        )
        assert len(facts) == 2
        first, second = facts
        assert first.fact_type == "preference"
        assert first.confidence == pytest.approx(0.9)
        assert first.keywords == ("checkout",)
        assert first.entity_id == "guest:1"
        assert first.source == "ep-9"
        # unknown category collapses to the default, empty content dropped
        assert second.fact_type == "info"

    def test_llm_extractor_degrades_on_garbage_and_errors(self):
        assert LLMFactExtractor(lambda p: "no json here").extract_facts([{"role": "user", "content": "x"}]) == []

        def _boom(prompt: str) -> str:
            raise RuntimeError("model down")

        assert LLMFactExtractor(_boom).extract_facts([{"role": "user", "content": "x"}]) == []
        assert LLMFactExtractor(lambda p: "[]").extract_facts([]) == []

    def test_extracted_fact_defaults(self):
        fact = ExtractedFact(fact_id="f1", content="x", fact_type="info")
        assert fact.confidence == 1.0
        assert fact.keywords == ()

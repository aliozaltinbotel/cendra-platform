"""Fake guest/PM conversation seeder for Cendra's unified_conversations ES index.

See `botelui/docs/superpowers/specs/2026-05-05-fake-conversation-seeder-design.md`.
"""
from __future__ import annotations

import argparse
import os
import random as _random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Final, Literal

from dotenv import load_dotenv


# ─── Stage clock ──────────────────────────────────────────────────────────

# Lambdas take (check_in, check_out) and return the Guest message time.
# One canonical entry per BookingStage value. The drift-guard test
# (`test_stage_offsets_cover_every_booking_stage`) asserts coverage.
STAGE_GUEST_OFFSETS: Final[dict[str, Callable[[datetime, datetime], datetime]]] = {
    "property_baseline": lambda ci, co: ci - timedelta(days=90),
    "pre_booking":       lambda ci, co: ci - timedelta(days=30),
    "booking_review":    lambda ci, co: ci - timedelta(days=14),
    "pre_arrival":       lambda ci, co: ci - timedelta(days=6),
    "checkin":           lambda ci, co: ci - timedelta(hours=2),
    "in_stay":           lambda ci, co: ci + (co - ci) * 0.30,
    "modification":      lambda ci, co: ci + (co - ci) * 0.20,
    "checkout":          lambda ci, co: co - timedelta(hours=2),
    "post_checkout":     lambda ci, co: co + timedelta(days=2),
    "ops":               lambda ci, co: ci + (co - ci) * 0.50,
}

PM_REPLY_DELAY_MINUTES: Final[int] = 5


def compute_message_times(
    stage: str,
    check_in: datetime,
    check_out: datetime,
    guest_offset_hours: int | None = None,
) -> tuple[datetime, datetime]:
    """Return (guest_at, pm_at) anchored at the requested stage.

    `guest_offset_hours` is reserved for a future override prompt; v1
    callers leave it None and inherit the stage default.
    """
    if guest_offset_hours is not None:
        guest_at = check_in + timedelta(hours=guest_offset_hours)
    else:
        guest_at = STAGE_GUEST_OFFSETS[stage](check_in, check_out)
    pm_at = guest_at + timedelta(minutes=PM_REPLY_DELAY_MINUTES)
    return guest_at, pm_at


# ─── Item builder ─────────────────────────────────────────────────────────

_VALID_SENDERS: Final[frozenset[str]] = frozenset({"guest", "property"})


def _format_iso_z(dt: datetime) -> str:
    """Format datetime as ISO-8601 with literal Z suffix.

    Converts the input to UTC first so a non-UTC tz-aware datetime is not
    silently mis-labelled as UTC. Naive datetimes pass through unchanged
    and are assumed to already represent UTC by the caller.
    """
    utc_dt = dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt
    return utc_dt.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def build_seed_item(
    *,
    parent_conversation_id: str,
    body: str,
    sender: Literal["guest", "property"],
    created_at: datetime,
    batch_id: str,
    stage: str,
) -> dict:
    """Build one camelCase message item conforming to unified_conversations.

    Marker fields (`isFakeSeed`, `seedBatchId`, `seedScenarioStage`) ride
    on each item; brain engine's GraphQL query does not select them, so
    they stay invisible to consumers but available to ES queries here.

    Raises:
        ValueError: when `sender` is not "guest" or "property".
    """
    if sender not in _VALID_SENDERS:
        raise ValueError(
            f"sender must be one of {sorted(_VALID_SENDERS)}, got {sender!r}"
        )

    item_uuid = str(uuid.uuid4())
    created_at_iso = _format_iso_z(created_at)
    return {
        "id": item_uuid,
        "messageId": parent_conversation_id,
        "pmsId": f"fake-seed-{item_uuid[:8]}",
        "body": body,
        "sender": sender,
        "createdAt": created_at_iso,
        "modifiedAt": created_at_iso,
        "messageType": "text",
        "communicationType": "chat",
        "sendByAI": False,
        "aiTag": None,
        "messageSentiment": None,
        "wasHelpful": None,
        "isFakeSeed": True,
        "seedBatchId": batch_id,
        "seedScenarioStage": stage,
    }


# ─── Prompts ──────────────────────────────────────────────────────────────

_MAX_PROMPT_ATTEMPTS: Final[int] = 3
_VALID_SELECTION_MODES: Final[tuple[str, ...]] = ("random", "most-messages", "most-recent")
_MAX_MESSAGE_LEN: Final[int] = 1000


class PromptAbort(RuntimeError):
    """Raised when an interactive prompt exhausts its retry budget."""


def _prompt_loop(prompt: str, validate: Callable[[str], object | None]) -> object:
    """Run an input loop, returning the validated value.

    `validate` returns the parsed value on success or None on failure
    (after printing its own error). Three Nones in a row → PromptAbort.
    """
    for _ in range(_MAX_PROMPT_ATTEMPTS):
        raw = input(prompt).strip()
        result = validate(raw)
        if result is not None:
            return result
    raise PromptAbort(f"Prompt {prompt!r} exhausted {_MAX_PROMPT_ATTEMPTS} attempts")


def prompt_property_id() -> str:
    def _validate(raw: str) -> str | None:
        if not raw:
            print("Boş olamaz, tekrar dene.")
            return None
        return raw

    return _prompt_loop("Property channel ID > ", _validate)  # type: ignore[return-value]


def prompt_total_count(eligible: int) -> int:
    def _validate(raw: str) -> int | None:
        try:
            n = int(raw)
        except ValueError:
            print(f"Sayı bekleniyor (1..{eligible}).")
            return None
        if n < 1:
            print("Pozitif bir sayı gir.")
            return None
        if n > eligible:
            print(f"{n} girdin ama sadece {eligible} eligible conversation var.")
            return None
        return n

    return _prompt_loop(f"Toplam mesaj sayısı (1..{eligible}) > ", _validate)  # type: ignore[return-value]


def prompt_stage_distribution(total: int) -> dict[str, int]:
    def _validate(raw: str) -> dict[str, int] | None:
        out: dict[str, int] = {}
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                print(f"'{chunk}' geçerli format değil. Beklenen: <stage>=<count>.")
                return None
            stage, _, num = chunk.partition("=")
            stage = stage.strip()
            if stage not in STAGE_GUEST_OFFSETS:
                print(
                    f"'{stage}' geçerli stage değil. Geçerliler: "
                    f"{', '.join(sorted(STAGE_GUEST_OFFSETS))}"
                )
                return None
            try:
                count = int(num.strip())
            except ValueError:
                print(f"'{num.strip()}' sayı değil.")
                return None
            if count < 1:
                print(f"'{stage}' için count en az 1 olmalı.")
                return None
            out[stage] = out.get(stage, 0) + count
        if not out:
            print("Distribüsyon boş olamaz. En az bir stage belirt.")
            return None
        if sum(out.values()) != total:
            print(f"Toplam {sum(out.values())}, ama {total} olması gerek.")
            return None
        return out

    return _prompt_loop(
        "Stage dağılımı (örn. pre_arrival=2, in_stay=8) > ", _validate
    )  # type: ignore[return-value]


def prompt_selection_mode() -> str:
    def _validate(raw: str) -> str | None:
        try:
            choice = int(raw)
        except ValueError:
            print("1, 2 veya 3 gir.")
            return None
        if not 1 <= choice <= 3:
            print("1, 2 veya 3 gir.")
            return None
        return _VALID_SELECTION_MODES[choice - 1]

    return _prompt_loop(
        "Selection [1=random, 2=most-messages, 3=most-recent] > ", _validate
    )  # type: ignore[return-value]


def prompt_message_pair(stage: str, *, count: int) -> tuple[str, str]:
    """Collect (guest, pm) for one stage. Aborts whole pair if either exhausts retries."""
    def _validate(raw: str) -> str | None:
        if not raw:
            print("Boş olamaz, tekrar dene.")
            return None
        if len(raw) > _MAX_MESSAGE_LEN:
            print(f"En fazla {_MAX_MESSAGE_LEN} karakter.")
            return None
        return raw

    print(f"\n  ─── {stage} ({count} conversation) ───")
    guest = _prompt_loop("  Guest mesajı > ", _validate)
    pm = _prompt_loop("  PM cevabı > ", _validate)
    return guest, pm  # type: ignore[return-value]


def prompt_confirm(prompt: str) -> bool:
    def _validate(raw: str) -> bool | None:
        low = raw.lower()
        if low in {"y", "yes"}:
            return True
        if low in {"n", "no"}:
            return False
        print("y veya n gir.")
        return None

    return _prompt_loop(f"{prompt} [y/N] > ", _validate)  # type: ignore[return-value]


# ─── ES read layer ────────────────────────────────────────────────────────

_FETCH_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "data.messageCount",
    "data.lastMessageAt",
    "data.booking.checkInDate",
    "data.booking.checkOutDate",
    "data.propertyChannelId",
)


def find_conversations_for_property(
    es,  # elasticsearch.Elasticsearch
    index: str,
    property_channel_id: str,
    *,
    max_candidates: int = 1000,
) -> list[dict]:
    """Return raw hits from a single search bounded to one property."""
    response = es.search(
        index=index,
        body={
            "size": max_candidates,
            "_source": list(_FETCH_FIELDS),
            "query": {"term": {"data.propertyChannelId": property_channel_id}},
        },
    )
    return list(response.get("hits", {}).get("hits", []))


def _parse_iso_utc(value: str | None) -> datetime | None:
    """Parse ISO-8601 string to UTC datetime.

    Accepts both tz-aware (`Z` or `±HH:MM`) and naive forms. Production
    `unified_conversations` data from Cendra's CDC commonly arrives
    without a tz suffix (e.g. `"2024-12-21T15:00:00"`); we assume UTC
    for those. Stage offsets are relative (check_in − 6 days etc.), so
    a uniform UTC assumption does not skew bucket math.
    """
    if not value or not isinstance(value, str):
        return None
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_booking_dates(hit: dict) -> tuple[datetime, datetime] | None:
    """Return (check_in, check_out) or None when either is unusable."""
    booking = hit.get("_source", {}).get("data", {}).get("booking") or {}
    ci = _parse_iso_utc(booking.get("checkInDate"))
    co = _parse_iso_utc(booking.get("checkOutDate"))
    if ci is None or co is None:
        return None
    return ci, co


def filter_eligible(hits: list[dict]) -> list[dict]:
    """Drop hits whose Booking dates do not parse."""
    return [h for h in hits if extract_booking_dates(h) is not None]


def pick_candidates(
    hits: list[dict],
    *,
    mode: str,
    n: int,
    rng_seed: int | None = None,
) -> list[dict]:
    """Order hits by selection mode and return the first `n`."""
    if n > len(hits):
        raise ValueError(
            f"{n} requested but only {len(hits)} eligible candidate(s) available"
        )
    if mode == "most-messages":
        return sorted(
            hits,
            key=lambda h: h["_source"]["data"].get("messageCount", 0),
            reverse=True,
        )[:n]
    if mode == "most-recent":
        return sorted(
            hits,
            key=lambda h: h["_source"]["data"].get("lastMessageAt") or "",
            reverse=True,
        )[:n]
    if mode == "random":
        rng = _random.Random(rng_seed)
        return rng.sample(hits, n)
    raise ValueError(f"unknown selection mode {mode!r}")


# ─── ES write layer + orchestrator ────────────────────────────────────────

APPEND_SCRIPT: Final[str] = """
ctx._source.data.messages.addAll(params.items);
ctx._source.data.messageCount = ctx._source.data.messages.size();
ctx._source.data.lastMessageAt = params.items[params.items.length - 1].createdAt;
""".strip()


def _append_to_conversation(
    es,
    index: str,
    doc_id: str,
    items: list[dict],
    routing: str | None = None,
) -> None:
    kwargs = {
        "index": index,
        "id": doc_id,
        "script": {"source": APPEND_SCRIPT, "params": {"items": items}},
        "refresh": "wait_for",
    }
    if routing:
        kwargs["routing"] = routing
    es.update(**kwargs)


def _build_es_client():
    """Construct an Elasticsearch client from .env. Lazily imports
    elasticsearch to keep the module importable without the dep
    (tests use a MagicMock instead).

    Accepts ES_HOST (URL form, for self-hosted / port-forwarded clusters)
    or ES_CLOUD_ID (Elastic Cloud base64 form). When the value looks like
    a URL we route it via hosts=[...] and disable cert verification —
    self-signed certs are the norm on Kubernetes-hosted Elasticsearch.
    """
    from elasticsearch import Elasticsearch  # type: ignore

    load_dotenv()
    host = os.getenv("ES_HOST") or ""
    cloud_id = os.getenv("ES_CLOUD_ID") or ""
    api_key = os.getenv("ES_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ES_API_KEY must be set (see tools/.env.example)"
        )

    # If ES_CLOUD_ID looks like a URL, treat it as a host instead.
    if cloud_id.startswith(("http://", "https://")):
        host = host or cloud_id
        cloud_id = ""

    if host:
        return Elasticsearch(hosts=[host], api_key=api_key, verify_certs=False)
    if cloud_id:
        return Elasticsearch(cloud_id=cloud_id, api_key=api_key)
    raise RuntimeError(
        "Either ES_HOST (URL) or ES_CLOUD_ID (Elastic Cloud id) must be set "
        "(see tools/.env.example)"
    )


def _get_index_name() -> str:
    load_dotenv()
    return os.getenv("UNIFIED_CONVERSATIONS_INDEX", "unified_conversations")


def run_seed(
    *,
    es,
    index: str,
    dry_run: bool,
    rng_seed: int | None = None,
) -> int:
    """Drive the interactive seed flow end-to-end. Returns exit code."""
    print("──────────────────────────────────────────────────────────────")
    print("Sandbox seeder — fake guest/PM mesajlarını unified_conversations'a ekler.")
    print("──────────────────────────────────────────────────────────────")
    try:
        property_id = prompt_property_id()
    except PromptAbort as exc:
        print(f"Aborted: {exc}")
        return 1

    print(f"\n   Searching unified_conversations for propertyChannelId={property_id}…")
    raw_hits = find_conversations_for_property(es, index, property_id)
    eligible = filter_eligible(raw_hits)
    print(f"   {len(raw_hits)} conversation bulundu.")
    print(f"   Booking.checkInDate / checkOutDate dolu olanlar: {len(eligible)} eligible.\n")

    if not eligible:
        print(f"ERROR: Property {property_id} için eligible conversation yok.")
        return 1

    try:
        total = prompt_total_count(eligible=len(eligible))
        distribution = prompt_stage_distribution(total=total)
        selection_mode = prompt_selection_mode()

        # One (guest, pm) per stage, applied to N conversations.
        contents: dict[str, tuple[str, str]] = {}
        print("\n[5] Şimdi her stage için mesaj içeriklerini gir.")
        for stage, count in distribution.items():
            contents[stage] = prompt_message_pair(stage, count=count)
    except PromptAbort as exc:
        print(f"Aborted: {exc}")
        return 1

    selected = pick_candidates(eligible, mode=selection_mode, n=total, rng_seed=rng_seed)
    batch_id = str(uuid.uuid4())

    # Build the plan: one (conversation, stage) pair per insertion.
    plan: list[tuple[dict, str]] = []
    cursor = 0
    for stage, count in distribution.items():
        for _ in range(count):
            plan.append((selected[cursor], stage))
            cursor += 1

    print("\n──────────────────────────────────────────────────────────────")
    print("PLAN ÖZETİ")
    print(f"  Property:           {property_id}")
    print(f"  Hedef conversation: {total} ({selection_mode})")
    print("  Insertions:")
    for stage, count in distribution.items():
        guest, pm = contents[stage]
        print(f"    {stage:18} × {count}  → {guest!r} / {pm!r}")
    print(f"  Marker batch_id:    {batch_id}")
    print("──────────────────────────────────────────────────────────────")

    try:
        proceed = prompt_confirm("Devam edilsin mi?")
    except PromptAbort as exc:
        print(f"Aborted: {exc}")
        return 1
    if not proceed:
        print("İptal edildi, hiçbir şey yazılmadı.")
        return 0

    print("\nInserting…" if not dry_run else "\n[dry-run] Plan:")
    for idx, (hit, stage) in enumerate(plan, start=1):
        ci, co = extract_booking_dates(hit)  # type: ignore[misc]
        guest_at, pm_at = compute_message_times(stage, ci, co)
        guest_text, pm_text = contents[stage]
        guest_item = build_seed_item(
            parent_conversation_id=hit["_id"],
            body=guest_text,
            sender="guest",
            created_at=guest_at,
            batch_id=batch_id,
            stage=stage,
        )
        pm_item = build_seed_item(
            parent_conversation_id=hit["_id"],
            body=pm_text,
            sender="property",
            created_at=pm_at,
            batch_id=batch_id,
            stage=stage,
        )
        prefix = f"[{idx:>2}/{len(plan)}] {hit['_id']}  {stage:14}"
        if dry_run:
            print(f"{prefix}  ci={ci.date()}  guest@{guest_at:%Y-%m-%d %H:%M}  (skipped)")
            continue
        try:
            _append_to_conversation(
                es,
                index,
                hit["_id"],
                [guest_item, pm_item],
                routing=hit.get("_routing"),
            )
            print(f"{prefix}  ci={ci.date()}  guest@{guest_at:%Y-%m-%d %H:%M}  ✓")
        except Exception as exc:  # pragma: no cover — surfaced to operator
            print(f"{prefix}  FAILED: {exc}")

    if not dry_run:
        items_inserted = len(plan) * 2  # guest + pm per plan entry
        print(
            f"\nTamamlandı.\n"
            f"  batch_id: {batch_id}\n"
            f"  {len(plan)} mesaj × 2 (Guest + PM) = {items_inserted} item, "
            f"{len(plan)} conversation'a eklendi."
        )
        print(f"  Cleanup için: python tools/seed_fake_conversations.py cleanup --batch-id {batch_id}")
    else:
        print(f"\n[dry-run] {len(plan)} planned insertion(s) for batch_id {batch_id}.")
    return 0


REMOVE_BATCH_SCRIPT: Final[str] = """
ctx._source.data.messages.removeIf(m -> m.seedBatchId == params.batch_id);
ctx._source.data.messageCount = ctx._source.data.messages.size();
if (ctx._source.data.messages.size() > 0) {
  def last = ctx._source.data.messages[ctx._source.data.messages.size() - 1];
  ctx._source.data.lastMessageAt = last.createdAt;
} else {
  ctx._source.data.lastMessageAt = null;
}
""".strip()

REMOVE_ALL_FAKE_SCRIPT: Final[str] = """
ctx._source.data.messages.removeIf(m -> m.isFakeSeed == true);
ctx._source.data.messageCount = ctx._source.data.messages.size();
if (ctx._source.data.messages.size() > 0) {
  def last = ctx._source.data.messages[ctx._source.data.messages.size() - 1];
  ctx._source.data.lastMessageAt = last.createdAt;
} else {
  ctx._source.data.lastMessageAt = null;
}
""".strip()


def run_cleanup(
    *,
    es,
    index: str,
    batch_id: str | None,
    remove_all: bool,
    dry_run: bool,
) -> int:
    # NOTE: Cendra's `unified_conversations` index has dynamic:false on the
    # nested messages mapping, so our marker fields (seedBatchId, isFakeSeed,
    # seedScenarioStage) live in _source but are NOT indexed. Term and nested
    # queries against them return zero hits. update_by_query with match_all
    # would scan every doc in the index and time out. We therefore scan
    # _source client-side, collect the doc ids that carry a marker, and
    # issue one targeted update per affected doc.
    if remove_all:
        script_source = REMOVE_ALL_FAKE_SCRIPT
        params: dict = {}
        label = "all isFakeSeed=true"
        marker_match = lambda m: bool(m.get("isFakeSeed"))  # noqa: E731
    else:
        if not batch_id:
            print("ERROR: --batch-id or --all is required.")
            return 1
        script_source = REMOVE_BATCH_SCRIPT
        params = {"batch_id": batch_id}
        label = f"batch {batch_id}"
        marker_match = lambda m: m.get("seedBatchId") == batch_id  # noqa: E731

    # Phase 1: scan _source to find affected (doc_id, routing) pairs.
    affected: list[tuple[str, str | None]] = []
    page_size = 500
    after: list | None = None
    while True:
        body: dict = {
            "size": page_size,
            "_source": ["data.messages"],
            "query": {"match_all": {}},
            "sort": [{"_doc": "asc"}],
        }
        if after is not None:
            body["search_after"] = after
        resp = es.search(index=index, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            msgs = h.get("_source", {}).get("data", {}).get("messages") or []
            if any(marker_match(m) for m in msgs):
                affected.append((h["_id"], h.get("_routing")))
        if len(hits) < page_size:
            break
        after = hits[-1]["sort"]

    if not affected:
        print(f"  No matches for {label}. Already cleaned up?")
        return 0

    if dry_run:
        print(f"[dry-run] {label}: {len(affected)} conversation(s) would be touched.")
        return 0

    print(f"Removing {label} from {index} ({len(affected)} doc(s) targeted)…")
    failed = 0
    for doc_id, routing in affected:
        kwargs = {
            "index": index,
            "id": doc_id,
            "script": {"source": script_source, "params": params},
            "refresh": "wait_for",
        }
        if routing:
            kwargs["routing"] = routing
        try:
            es.update(**kwargs)
        except Exception as exc:  # pragma: no cover — surfaced to operator
            print(f"  FAILED {doc_id[:40]}…: {exc}")
            failed += 1
    updated = len(affected) - failed
    if updated:
        print(f"  ✓ {updated} conversation updated.")
    if failed:
        print(f"  ✗ {failed} conversation failed.")
    return 0


def run_list_batches(*, es, index: str, dry_run: bool) -> int:
    """Scan unified_conversations docs and group seed items by batch_id.

    The marker fields (`seedBatchId`, `isFakeSeed`, `seedScenarioStage`) are
    not indexed on Cendra's `unified_conversations` (dynamic:false on the
    nested messages mapping). Aggregations against them silently return
    zero buckets. We therefore scan _source client-side and group in
    Python — slower but mapping-independent.
    """
    if dry_run:
        print("[dry-run] list-batches is read-only; running normally.")

    # Scroll through all docs, fetching only the messages we need.
    page_size = 500
    batches: dict[str, dict] = {}
    after: list | None = None
    while True:
        body: dict = {
            "size": page_size,
            "_source": ["data.messages"],
            "query": {"match_all": {}},
            "sort": [{"_doc": "asc"}],
        }
        if after is not None:
            body["search_after"] = after
        resp = es.search(index=index, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            for m in h.get("_source", {}).get("data", {}).get("messages") or []:
                bid = m.get("seedBatchId")
                if not bid:
                    continue
                bucket = batches.setdefault(
                    bid,
                    {"item_count": 0, "stages": {}, "conv_ids": set(),
                     "earliest": None},
                )
                bucket["item_count"] += 1
                bucket["conv_ids"].add(h["_id"])
                stage = m.get("seedScenarioStage", "?")
                bucket["stages"][stage] = bucket["stages"].get(stage, 0) + 1
                created = m.get("createdAt") or ""
                if bucket["earliest"] is None or created < bucket["earliest"]:
                    bucket["earliest"] = created
        if len(hits) < page_size:
            break
        after = hits[-1]["sort"]

    if not batches:
        print("No active seed batches in unified_conversations.")
        return 0

    print(f"\nActive seed batches in {index}:\n")
    print(f"  {'batch_id':40}  {'items':>5}  {'convs':>5}  {'stages':30}  earliest")
    print(f"  {'-'*40}  {'-'*5}  {'-'*5}  {'-'*30}  {'-'*20}")
    for bid in sorted(batches, key=lambda b: batches[b]["earliest"] or ""):
        b = batches[bid]
        stages_summary = ", ".join(f"{s}×{c}" for s, c in sorted(b["stages"].items()))
        earliest = b["earliest"] or "—"
        print(
            f"  {bid:40}  {b['item_count']:>5}  {len(b['conv_ids']):>5}  "
            f"{stages_summary:30}  {earliest}"
        )
    print()
    return 0


def run_show_batch(*, es, index: str, batch_id: str, dry_run: bool) -> int:
    """Show per-conversation detail for a single seed batch.

    Scans unified_conversations with match_all + search_after, collects every
    message item where seedBatchId == batch_id, and prints a fixed-width table
    with full doc id, property, stage, sender, createdAt and body (50 chars).
    """
    if dry_run:
        print("[dry-run] show-batch is read-only; running normally.")

    page_size = 500
    rows: list[tuple[str, str, str, str, str, str]] = []  # (doc_id, property, stage, sender, created_at, body)
    conv_ids: set[str] = set()
    after: list | None = None

    while True:
        body: dict = {
            "size": page_size,
            "_source": ["data.messages", "data.propertyChannelId", "data.title"],
            "query": {"match_all": {}},
            "sort": [{"_doc": "asc"}],
        }
        if after is not None:
            body["search_after"] = after
        resp = es.search(index=index, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            doc_id = h["_id"]
            src = h.get("_source", {})
            data = src.get("data", {})
            property_id = data.get("propertyChannelId", "")
            for m in data.get("messages") or []:
                if m.get("seedBatchId") != batch_id:
                    continue
                stage = m.get("seedScenarioStage", "")
                sender = m.get("sender", "")
                created_at = m.get("createdAt", "")
                raw_body = m.get("body", "") or ""
                body_flat = raw_body.replace("\n", " ").replace("\r", " ")
                body_snippet = body_flat[:50]
                rows.append((doc_id, property_id, stage, sender, created_at, body_snippet))
                conv_ids.add(doc_id)
        if len(hits) < page_size:
            break
        after = hits[-1]["sort"]

    if not rows:
        print(f"No items found for batch {batch_id}.")
        return 0

    # Sort by (doc_id, createdAt) so guest+pm of the same conversation stay adjacent.
    rows.sort(key=lambda r: (r[0], r[4]))

    header = (
        f"{'doc_id':<}  {'property':>8}  {'stage':<15}  {'sender':<8}  {'created_at':<24}  body"
    )
    sep = f"{'-'*36}  {'-'*8}  {'-'*15}  {'-'*8}  {'-'*24}  {'-'*50}"
    print(header)
    print(sep)
    for doc_id, property_id, stage, sender, created_at, body_snippet in rows:
        print(f"{doc_id}  {property_id:>8}  {stage:<15}  {sender:<8}  {created_at:<24}  {body_snippet}")

    n_items = len(rows)
    n_convs = len(conv_ids)
    print(f"\n{n_items} items in {n_convs} conversation(s) for batch {batch_id}.")
    return 0


# ─── CLI entry ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdout so Windows cp1252 cannot blow up on Unicode
    # box-drawing or Turkish characters in the interactive prompts.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(prog="seed_fake_conversations")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="Interactively seed fake conversations.")

    cleanup = sub.add_parser("cleanup", help="Remove seeded items.")
    cleanup_group = cleanup.add_mutually_exclusive_group(required=True)
    cleanup_group.add_argument("--batch-id", help="Remove a specific seed batch.")
    cleanup_group.add_argument(
        "--all", action="store_true", help="Remove every isFakeSeed=true item."
    )

    sub.add_parser("list-batches", help="Tabulate active seed batches.")

    show = sub.add_parser("show-batch", help="Show conversations affected by one batch.")
    show.add_argument("--batch-id", required=True, help="Batch id to inspect.")

    parser.add_argument("--dry-run", action="store_true",
                        help="Run read-side queries; print plan; skip writes.")

    args = parser.parse_args(argv)
    if args.cmd == "seed":
        es = _build_es_client()
        index = _get_index_name()
        return run_seed(es=es, index=index, dry_run=args.dry_run)
    if args.cmd == "cleanup":
        es = _build_es_client()
        index = _get_index_name()
        return run_cleanup(
            es=es,
            index=index,
            batch_id=args.batch_id,
            remove_all=args.all,
            dry_run=args.dry_run,
        )
    if args.cmd == "list-batches":
        es = _build_es_client()
        index = _get_index_name()
        return run_list_batches(es=es, index=index, dry_run=args.dry_run)
    if args.cmd == "show-batch":
        es = _build_es_client()
        index = _get_index_name()
        return run_show_batch(es=es, index=index, batch_id=args.batch_id, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())

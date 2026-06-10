"""Brain Engine live showcase — proof-of-power operator CLI.

The showcase is a deliberate flex: it walks every cognitive layer of
the deployed Brain Engine and prints a coloured terminal report so a
PM can see, in one screen, that memory, patterns, refusals, time-
window classification, and observability are alive together.

Two classes of probes run side-by-side:

* **HTTP probes** hit the live API at ``--base-url`` (default
  ``https://brain-engine-dev.botel.ai``) and read public-by-design
  admin endpoints — ``/health``, ``/api/admin/memory/*``,
  ``/api/admin/past-conversations*``, ``/metrics``.  Each probe
  degrades to a yellow ``n/a`` line if the endpoint is missing or
  unauthorised so the report stays readable on partial deployments.

* **In-process demos** import the runtime modules and run them on
  synthetic inputs — a Turkish refusal sentence, a reservation due
  in three hours — to demonstrate that the code shipped on the
  deployed image *can* do what its docstrings claim.

Designed for live demos.  No write-side calls.  No mutations.  No
dependencies beyond ``httpx`` (already in ``requirements.txt``) and
the engine's own modules.

Usage::

    python3 -m tools.brain_engine_showcase \\
        --base-url https://brain-engine-dev.botel.ai \\
        --property-id 323133 \\
        --reservation-id 12345

Exit code is 0 when every required probe is OK, 1 when any required
probe fails.  ``n/a`` (endpoint missing) does **not** fail the run —
that is the whole point of a degrade-friendly showcase.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import httpx


# ---------------------------------------------------------------------------
# Terminal styling
# ---------------------------------------------------------------------------

_RESET: Final[str] = "\033[0m"
_BOLD: Final[str] = "\033[1m"
_DIM: Final[str] = "\033[2m"
_GREEN: Final[str] = "\033[32m"
_YELLOW: Final[str] = "\033[33m"
_RED: Final[str] = "\033[31m"
_CYAN: Final[str] = "\033[36m"
_MAGENTA: Final[str] = "\033[35m"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _err(text: str) -> str:
    return f"{_RED}{text}{_RESET}"


def _hl(text: str) -> str:
    return f"{_CYAN}{text}{_RESET}"


def _accent(text: str) -> str:
    return f"{_MAGENTA}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


# ---------------------------------------------------------------------------
# Probe result model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProbeResult:
    """Outcome of one probe section.

    Attributes:
        name: Human label for the section.
        status: ``ok`` / ``na`` / ``fail``.
        lines: Pre-formatted lines printed under the section header.
        elapsed_ms: Wall-clock time the probe took.
    """

    name: str
    status: str = "na"
    lines: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def is_fail(self) -> bool:
        return self.status == "fail"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """GET ``path`` and return ``(status, body)``.

    ``body`` is the parsed JSON document on 2xx, the raw text on
    other codes, or ``None`` when the request fails outright.
    """
    try:
        resp = await client.get(path, params=params)
    except httpx.HTTPError as exc:
        return 0, f"transport error: {exc!r}"
    if 200 <= resp.status_code < 300:
        try:
            return resp.status_code, resp.json()
        except json.JSONDecodeError:
            return resp.status_code, resp.text
    return resp.status_code, resp.text


def _format_count(value: Any, suffix: str = "") -> str:
    """Format an int-or-None counter for the report tree."""
    if isinstance(value, int):
        return f"{value:,}{suffix}"
    if isinstance(value, float):
        return f"{value:,.2f}{suffix}"
    return "n/a"


def _elapsed_ms(started: datetime) -> float:
    """Return milliseconds since ``started`` as a float."""
    delta = datetime.now(timezone.utc) - started
    return delta.total_seconds() * 1000.0


# ---------------------------------------------------------------------------
# Section: health
# ---------------------------------------------------------------------------


async def probe_health(client: httpx.AsyncClient) -> ProbeResult:
    """Hit ``/health`` and report uptime / version when available."""
    started = datetime.now(timezone.utc)
    status, body = await _get_json(client, "/health")
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000

    if status != 200 or not isinstance(body, dict):
        return ProbeResult(
            name="Health",
            status="fail",
            lines=[_err(f"  /health returned {status}: {body}")],
            elapsed_ms=elapsed,
        )

    lines: list[str] = []
    pod_status = body.get("status", "unknown")
    lines.append(f"  status     : {_ok(pod_status)}")
    if "uptime_seconds" in body:
        uptime = _format_count(body["uptime_seconds"], " s")
        lines.append(f"  uptime     : {_hl(uptime)}")
    if "version" in body:
        lines.append(f"  version    : {_hl(str(body['version']))}")
    if "git_sha" in body:
        lines.append(f"  git_sha    : {_dim(str(body['git_sha'])[:12])}")
    return ProbeResult(
        name="Health",
        status="ok",
        lines=lines,
        elapsed_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Section: memory layers
# ---------------------------------------------------------------------------


async def probe_memory_status(
    client: httpx.AsyncClient,
) -> ProbeResult:
    """Read ``/api/admin/memory/status`` and pretty-print the tiers."""
    started = datetime.now(timezone.utc)
    status, body = await _get_json(client, "/api/admin/memory/status")
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000

    if status == 404:
        return ProbeResult(
            name="Memory layers",
            status="na",
            lines=[
                _warn(
                    "  endpoint not deployed "
                    "(PR 169 / Stage 8.3 pending)"
                )
            ],
            elapsed_ms=elapsed,
        )
    if status != 200 or not isinstance(body, dict):
        return ProbeResult(
            name="Memory layers",
            status="fail",
            lines=[_err(f"  /memory/status returned {status}: {body}")],
            elapsed_ms=elapsed,
        )

    lines: list[str] = []
    for tier in ("episodic", "cases", "rules"):
        block = body.get(tier, {}) or {}
        ready = bool(block.get("ready"))
        marker = _ok("ready") if ready else _warn("not_ready")
        size = (
            block.get("count")
            or block.get("total")
            or block.get("size")
        )
        if size is not None:
            size_text = _hl(_format_count(size))
        else:
            size_text = _dim("n/a")
        lines.append(
            f"  {tier:<10} : {marker:<14}  size {size_text}"
        )
        backend = block.get("backend")
        if backend:
            lines.append(f"             {_dim('backend ' + str(backend))}")

    overall_ok = all(
        bool((body.get(t, {}) or {}).get("ready"))
        for t in ("episodic", "cases", "rules")
    )
    return ProbeResult(
        name="Memory layers",
        status="ok" if overall_ok else "fail",
        lines=lines,
        elapsed_ms=elapsed,
    )


async def probe_memory_recent(
    client: httpx.AsyncClient,
    property_id: str,
    limit: int,
) -> ProbeResult:
    """Read ``/api/admin/memory/recent/{property_id}``."""
    started = datetime.now(timezone.utc)
    path = f"/api/admin/memory/recent/{property_id}"
    status, body = await _get_json(client, path, params={"limit": limit})
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000

    if status == 404:
        return ProbeResult(
            name=f"Recent memory for {property_id}",
            status="na",
            lines=[_warn("  endpoint not deployed")],
            elapsed_ms=elapsed,
        )
    if status != 200 or not isinstance(body, dict):
        return ProbeResult(
            name=f"Recent memory for {property_id}",
            status="fail",
            lines=[_err(f"  returned {status}: {body}")],
            elapsed_ms=elapsed,
        )

    episodes = body.get("episodes") or []
    cases = body.get("cases") or []
    lines = [
        f"  episodes   : {_hl(_format_count(len(episodes)))}",
        f"  cases      : {_hl(_format_count(len(cases)))}",
    ]
    for episode in episodes[:3]:
        ts = episode.get("created_at") or episode.get("timestamp") or "?"
        kind = episode.get("kind") or episode.get("event_type") or "?"
        lines.append(f"    {_dim('•')} {ts}  {_accent(str(kind))}")
    return ProbeResult(
        name=f"Recent memory for {property_id}",
        status="ok",
        lines=lines,
        elapsed_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Section: past conversations
# ---------------------------------------------------------------------------


async def probe_past_conversations(
    client: httpx.AsyncClient,
    property_id: str,
    limit: int,
) -> ProbeResult:
    """List past-conversation cases for one property."""
    started = datetime.now(timezone.utc)
    path = "/api/admin/past-conversations"
    status, body = await _get_json(
        client,
        path,
        params={"property_id": property_id, "limit": limit},
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000

    if status == 404:
        return ProbeResult(
            name="Past conversations",
            status="na",
            lines=[_warn("  Stage 8.3 router not deployed yet")],
            elapsed_ms=elapsed,
        )
    if status != 200 or not isinstance(body, dict):
        return ProbeResult(
            name="Past conversations",
            status="fail",
            lines=[_err(f"  returned {status}: {body}")],
            elapsed_ms=elapsed,
        )

    cases = body.get("cases") or []
    lines = [f"  cases shown : {_hl(_format_count(len(cases)))}"]
    by_stage: dict[str, int] = {}
    by_scenario: dict[str, int] = {}
    for case in cases:
        stage_key = case.get("stage", "?")
        scen_key = case.get("scenario", "?")
        by_stage[stage_key] = by_stage.get(stage_key, 0) + 1
        by_scenario[scen_key] = by_scenario.get(scen_key, 0) + 1
    top_stages = sorted(by_stage.items(), key=lambda x: -x[1])[:5]
    for stage, count in top_stages:
        lines.append(
            f"    {_dim('•')} stage     {stage:<22} "
            f"{_hl(str(count))}"
        )
    top_scen = sorted(by_scenario.items(), key=lambda x: -x[1])[:5]
    for scenario, count in top_scen:
        lines.append(
            f"    {_dim('•')} scenario  {scenario:<22} {_hl(str(count))}"
        )
    return ProbeResult(
        name="Past conversations",
        status="ok",
        lines=lines,
        elapsed_ms=elapsed,
    )


async def probe_reservation_analysis(
    client: httpx.AsyncClient,
    reservation_id: str,
) -> ProbeResult:
    """Hit the per-reservation analysis endpoint and surface refusals."""
    started = datetime.now(timezone.utc)
    path = f"/api/admin/past-conversations/{reservation_id}/analysis"
    status, body = await _get_json(client, path)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000

    if status == 404:
        return ProbeResult(
            name=f"Analysis for reservation {reservation_id}",
            status="na",
            lines=[_warn("  Stage 8.3 router not deployed yet")],
            elapsed_ms=elapsed,
        )
    if status != 200 or not isinstance(body, dict):
        return ProbeResult(
            name=f"Analysis for reservation {reservation_id}",
            status="fail",
            lines=[_err(f"  returned {status}: {body}")],
            elapsed_ms=elapsed,
        )

    cases = body.get("cases") or []
    refusals = body.get("refusal_signals") or []
    histograms = body.get("histograms") or {}
    lines = [
        f"  cases             : {_hl(_format_count(len(cases)))}",
        f"  refusal signals   : {_hl(_format_count(len(refusals)))}",
    ]
    for refusal in refusals[:3]:
        rtype = refusal.get("refusal_type", "?")
        lang = refusal.get("language", "?")
        confidence = refusal.get("confidence", "?")
        lines.append(
            f"    {_dim('•')} {_accent(str(rtype)):<24}"
            f" lang={lang} conf={confidence}"
        )
    for hist_name, hist in list(histograms.items())[:3]:
        if isinstance(hist, dict):
            top = sorted(hist.items(), key=lambda x: -x[1])[:3]
            preview = ", ".join(f"{k}={v}" for k, v in top)
            lines.append(f"    {_dim('•')} {hist_name}: {preview}")
    return ProbeResult(
        name=f"Analysis for reservation {reservation_id}",
        status="ok",
        lines=lines,
        elapsed_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Section: in-process demos (refusal extractor + classifier)
# ---------------------------------------------------------------------------


def demo_refusal_extractor() -> ProbeResult:
    """Run the bundled RefusalExtractor on a multilingual sample."""
    started = datetime.now(timezone.utc)
    samples = [
        ("tr", "Pasaport vermeden kapı kodunu paylaşamam"),
        ("en", "I cannot share the wifi without ID verification"),
        ("ru", "Без паспорта код двери не дам"),
        ("es", "Sin pasaporte no puedo enviar el código"),
    ]
    try:
        from brain_engine.patterns.refusal_extractor import (
            RefusalExtractor,
        )
    except ImportError as exc:
        return ProbeResult(
            name="In-process refusal extractor",
            status="na",
            lines=[_warn(f"  module not importable: {exc!r}")],
            elapsed_ms=0.0,
        )

    extractor = RefusalExtractor()
    lines: list[str] = []
    detected = 0
    for lang, text in samples:
        signal = extractor.extract(text)
        if signal is not None:
            detected += 1
            rtype = getattr(signal, "refusal_type", None)
            rtype_value = getattr(rtype, "value", str(rtype))
            lines.append(
                f"  {_accent(lang.upper())}  {text[:48]:<48} "
                f"-> {_ok(str(rtype_value))}"
            )
        else:
            lines.append(
                f"  {_accent(lang.upper())}  {text[:48]:<48} -> {_dim('-')}"
            )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    return ProbeResult(
        name="In-process refusal extractor (Stage 8.2)",
        status="ok" if detected >= 1 else "fail",
        lines=lines + [f"  detected: {_hl(str(detected))} / {len(samples)}"],
        elapsed_ms=elapsed,
    )


def demo_time_window_classifier() -> ProbeResult:
    """Show date-aware stage classification at 24h / 4h / in-stay."""
    started = datetime.now(timezone.utc)
    try:
        from brain_engine.patterns.classifier import DecisionClassifier
        from brain_engine.patterns.feature_builder import FeatureBuilder
    except ImportError as exc:
        return ProbeResult(
            name="In-process time-window classifier",
            status="na",
            lines=[_warn(f"  module not importable: {exc!r}")],
            elapsed_ms=0.0,
        )

    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    h = timedelta(hours=1)
    d = timedelta(days=1)
    cases = [
        ("48h before check-in", now + 48 * h, now + 5 * d),
        ("3h before check-in", now + 3 * h, now + 5 * d),
        ("first hour of stay", now - h, now + 2 * d),
        ("last hour of stay", now - 2 * d, now + h),
        ("after checkout", now - 3 * d, now - 2 * h),
    ]

    fb = FeatureBuilder()
    classifier = DecisionClassifier()
    lines: list[str] = []
    for label, check_in, check_out in cases:
        reservation = {
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
        }
        features = fb.build(reservation, now=now)
        stage = classifier.classify(
            message_text="hi",
            reservation=reservation,
            now=now,
        )
        stage_value = getattr(stage, "stage", stage)
        stage_text = getattr(stage_value, "value", str(stage_value))
        hours = getattr(features, "hours_before_checkin", "?")
        lines.append(
            f"  {label:<22} hours_before_checkin={hours:<+8}"
            f"  -> stage {_accent(str(stage_text))}"
        )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    return ProbeResult(
        name="In-process date-aware classifier (Stage 8.1)",
        status="ok",
        lines=lines,
        elapsed_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Section: observability
# ---------------------------------------------------------------------------


async def probe_metrics(client: httpx.AsyncClient) -> ProbeResult:
    """Read ``/metrics`` and count exposed series."""
    started = datetime.now(timezone.utc)
    try:
        resp = await client.get("/metrics")
    except httpx.HTTPError as exc:
        return ProbeResult(
            name="Observability — Prometheus /metrics",
            status="fail",
            lines=[_err(f"  transport error: {exc!r}")],
            elapsed_ms=_elapsed_ms(started),
        )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000

    if resp.status_code == 404:
        return ProbeResult(
            name="Observability — Prometheus /metrics",
            status="na",
            lines=[_warn("  /metrics not exposed by this build")],
            elapsed_ms=elapsed,
        )
    if resp.status_code != 200:
        return ProbeResult(
            name="Observability — Prometheus /metrics",
            status="fail",
            lines=[_err(f"  returned {resp.status_code}: {resp.text[:200]}")],
            elapsed_ms=elapsed,
        )

    families = {
        line.split(" ", 1)[1].split("{", 1)[0].strip()
        for line in resp.text.splitlines()
        if line.startswith("# TYPE ")
    }
    sample_lines = [
        line for line in resp.text.splitlines()
        if line and not line.startswith("#")
    ]
    return ProbeResult(
        name="Observability — Prometheus /metrics",
        status="ok",
        lines=[
            f"  metric families : {_hl(_format_count(len(families)))}",
            f"  series          : {_hl(_format_count(len(sample_lines)))}",
            f"  examples        : "
            f"{_dim(', '.join(sorted(families)[:4]))}",
        ],
        elapsed_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _print_section(result: ProbeResult, index: int, total: int) -> None:
    if result.is_ok:
        marker = _ok("OK ")
    elif result.is_fail:
        marker = _err("FAIL")
    else:
        marker = _warn("n/a")
    header = (
        f"{_BOLD}[{index}/{total}]{_RESET} "
        f"{result.name:<48} {marker}  "
        f"{_dim(f'{result.elapsed_ms:6.1f} ms')}"
    )
    print(header)
    for line in result.lines:
        print(line)
    print()


async def _run_probes(
    base_url: str,
    property_id: str,
    reservation_id: str | None,
    limit: int,
    timeout_s: float,
) -> list[ProbeResult]:
    """Sequentially execute every probe and return the results list."""
    transport = httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout_s,
        follow_redirects=True,
    )
    async with transport as client:
        probes: list[Callable[[], Awaitable[ProbeResult]]] = [
            lambda: probe_health(client),
            lambda: probe_memory_status(client),
            lambda: probe_memory_recent(client, property_id, limit),
            lambda: probe_past_conversations(client, property_id, limit),
        ]
        if reservation_id:
            probes.append(
                lambda: probe_reservation_analysis(client, reservation_id),
            )
        probes.append(lambda: probe_metrics(client))
        results: list[ProbeResult] = []
        for probe in probes:
            results.append(await probe())

    # In-process demos do not need the HTTP client.
    results.append(_run_sync(demo_refusal_extractor))
    results.append(_run_sync(demo_time_window_classifier))
    return results


def _run_sync(fn: Callable[[], ProbeResult]) -> ProbeResult:
    """Wrap a synchronous demo function into the same result list."""
    return fn()


def _summary_banner(results: list[ProbeResult]) -> tuple[str, int]:
    """Build the closing banner and return (text, exit_code)."""
    ok_count = sum(1 for r in results if r.is_ok)
    fail_count = sum(1 for r in results if r.is_fail)
    na_count = sum(1 for r in results if not r.is_ok and not r.is_fail)
    if fail_count == 0 and ok_count >= 1:
        verdict = _ok("BRAIN ENGINE GREEN — all required layers alive")
        exit_code = 0
    elif fail_count == 0:
        verdict = _warn(
            "BRAIN ENGINE PARTIAL — every required layer was n/a"
        )
        exit_code = 0
    else:
        verdict = _err(
            f"BRAIN ENGINE DEGRADED — {fail_count} layer(s) failing"
        )
        exit_code = 1
    bar = "═" * 60
    body = (
        f"\n{bar}\n"
        f"  {verdict}\n"
        f"  ok={_ok(str(ok_count))}  "
        f"fail={_err(str(fail_count))}  "
        f"n/a={_warn(str(na_count))}\n"
        f"{bar}\n"
    )
    return body, exit_code


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="brain_engine_showcase",
        description=(
            "Walk every Brain Engine layer (health / memory / "
            "patterns / refusals / observability) and print a "
            "coloured terminal report."
        ),
    )
    parser.add_argument(
        "--base-url",
        default="https://brain-engine-dev.botel.ai",
        help="Brain Engine ingress URL (default: dev).",
    )
    parser.add_argument(
        "--property-id",
        default="323133",
        help="Property to query for memory/cases (default: ZAŽI dev).",
    )
    parser.add_argument(
        "--reservation-id",
        default=None,
        help="Optional reservation id for the analysis probe.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit for list-style probes (default: 20).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: 10).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns process exit code."""
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    print(f"\n{_BOLD}🧠 BRAIN ENGINE SHOWCASE{_RESET}")
    print(f"  base_url      : {_hl(args.base_url)}")
    print(f"  property_id   : {_hl(args.property_id)}")
    if args.reservation_id:
        print(f"  reservation   : {_hl(args.reservation_id)}")
    print(
        f"  started       : "
        f"{_hl(datetime.now(timezone.utc).isoformat(timespec='seconds'))}"
    )
    print()

    results = asyncio.run(
        _run_probes(
            base_url=args.base_url,
            property_id=args.property_id,
            reservation_id=args.reservation_id,
            limit=args.limit,
            timeout_s=args.timeout,
        )
    )

    total = len(results)
    for index, result in enumerate(results, start=1):
        _print_section(result, index, total)

    banner, exit_code = _summary_banner(results)
    print(banner)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

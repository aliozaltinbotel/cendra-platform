#!/usr/bin/env python3
"""Live terminal monitor for Brain Engine memory + patterns.

Polls ``/api/admin/memory/status`` (and optionally
``/api/admin/memory/recent/{property_id}``) at a configurable cadence
and renders a compact, refreshing snapshot of every tier.

Designed for two ergonomic shapes:

* From a developer machine with a port-forward:
  ``kubectl -n dev port-forward svc/brain-engine 8080:80 &
  python tools/memory_monitor.py --base-url http://localhost:8080``

* From inside the cluster (e.g. an admin pod):
  ``python tools/memory_monitor.py
  --base-url http://brain-engine.dev.svc.cluster.local``

The monitor is read-only.  It never POSTs.  Output is plain ANSI;
piping into ``tee`` keeps a textual audit trail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx


_CSI_CLEAR = "\x1b[2J\x1b[H"


async def _fetch(
    client: httpx.AsyncClient,
    path: str,
) -> dict[str, Any] | None:
    """Fetch JSON from ``path`` or return ``None`` on transport error."""
    try:
        response = await client.get(path, timeout=5.0)
    except httpx.HTTPError as exc:
        return {"_error": f"{exc.__class__.__name__}: {exc}"}
    if response.status_code != 200:
        return {
            "_error": (
                f"HTTP {response.status_code}: "
                f"{response.text[:200]}"
            ),
        }
    try:
        return response.json()
    except json.JSONDecodeError:
        return {"_error": "non-JSON response"}


def _render(
    status: dict[str, Any],
    recent: dict[str, Any] | None,
) -> str:
    """Render one frame of the dashboard as a string."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Brain Engine — Memory Monitor")
    lines.append("=" * 72)
    if "_error" in status:
        lines.append(f"  status error: {status['_error']}")
        return "\n".join(lines)

    overall = "READY" if status.get("ready") else "DEGRADED"
    lines.append(f"  overall: {overall}")
    lines.append("")

    ep = status.get("episodic", {})
    lines.append("[episodic memory]")
    if ep.get("ready"):
        lines.append(
            f"  session={ep.get('session_id', '')!s}  "
            f"recent_sample={ep.get('recent_sample_size', 0)}"
        )
        lines.append(
            f"  latest_event={ep.get('latest_event')!s}  "
            f"at={ep.get('latest_timestamp')!s}"
        )
    else:
        lines.append(f"  not ready — {ep.get('reason', 'unknown')}")
    lines.append("")

    cases = status.get("decision_cases", {})
    lines.append("[decision cases]")
    if cases.get("ready"):
        lines.append(
            f"  total={cases.get('total', 0)}  "
            f"store={cases.get('store_class', '?')}"
        )
    else:
        lines.append(f"  not ready — {cases.get('reason', 'unknown')}")
    lines.append("")

    rules = status.get("pattern_rules", {})
    lines.append("[pattern rules]")
    if rules.get("ready"):
        lines.append(
            f"  active={rules.get('active', 0)}  "
            f"store={rules.get('store_class', '?')}"
        )
        lines.append(f"  by_mode={rules.get('by_mode', {})}")
        lines.append(f"  by_scope={rules.get('by_scope', {})}")
    else:
        lines.append(f"  not ready — {rules.get('reason', 'unknown')}")
    lines.append("")

    if recent is not None:
        lines.append("=" * 72)
        prop = recent.get("property_id", "?")
        lines.append(f"property snapshot: {prop}")
        lines.append("-" * 72)
        if "_error" in recent:
            lines.append(f"  recent error: {recent['_error']}")
        else:
            episodes = recent.get("episodes", []) or []
            cases_list = recent.get("cases", []) or []
            lines.append(
                f"  episodes={len(episodes)}  cases={len(cases_list)}"
            )
            for ep_item in episodes[:5]:
                lines.append(
                    f"  ep: {ep_item.get('event', '?')[:24]:<24}  "
                    f"{ep_item.get('timestamp', '?')}"
                )
            for case_item in cases_list[:5]:
                lines.append(
                    f"  case: {case_item.get('scenario', '?')[:20]:<20} "
                    f"stage={case_item.get('stage', '?')[:14]:<14} "
                    f"dec={case_item.get('decision', '?')}"
                )

    return "\n".join(lines)


async def _loop(
    base_url: str,
    property_id: str | None,
    interval: float,
    once: bool,
) -> int:
    """Run the monitor loop until ``once`` is satisfied or Ctrl-C."""
    async with httpx.AsyncClient(base_url=base_url) as client:
        while True:
            status = await _fetch(client, "/api/admin/memory/status")
            recent: dict[str, Any] | None = None
            if property_id:
                recent = await _fetch(
                    client,
                    f"/api/admin/memory/recent/{property_id}",
                )
            sys.stdout.write(_CSI_CLEAR)
            sys.stdout.write(_render(status or {}, recent))
            sys.stdout.write("\n")
            sys.stdout.flush()
            if once:
                return 0
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Live monitor for Brain Engine memory + patterns",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8080",
        help="Brain Engine base URL (no trailing slash).",
    )
    parser.add_argument(
        "--property-id",
        default=None,
        help=(
            "Optional: render a per-property recent panel as well."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Poll interval in seconds.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Render a single frame and exit (for cron / CI).",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(
            _loop(
                base_url=args.base_url.rstrip("/"),
                property_id=args.property_id,
                interval=args.interval,
                once=args.once,
            )
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

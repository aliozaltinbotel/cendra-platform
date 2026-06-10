#!/usr/bin/env bash
# Per-module coverage gate enforcement for Brain Engine.
#
# Runs ``coverage report --fail-under`` once per safety tier and
# fails the pipeline if any tier slips.  Tiers reflect blast
# radius: a guardrail false-negative or an approval mis-route can
# cost an owner real money, so they ride at 95%.  Conversation and
# memory live one tier below because their tests still depend on
# fixtures we are growing.  Integrations sit at the floor — we
# expect them to be exercised end-to-end, not unit-tested.
#
# The script assumes ``coverage run -m pytest`` has already been
# executed (so .coverage exists at repo root).

set -euo pipefail

declare -A TIERS=(
    [guardrails]="brain_engine/guardrails:95"
    [approval]="brain_engine/approval:95"
    [blockers]="brain_engine/blockers:90"
    [zfs]="brain_engine/zfs:85"
    [memory_cascade]="brain_engine/memory/cascade:90"
    [conversation]="brain_engine/conversation:80"
    [compliance]="brain_engine/compliance:85"
    [models]="brain_engine/models:85"
    [api_server]="api_server:75"
    [interview]="brain_engine/interview:70"
    [narrative]="brain_engine/narrative:70"
)

FAILED=0
for tier in "${!TIERS[@]}"; do
    spec="${TIERS[$tier]}"
    path="${spec%%:*}"
    floor="${spec##*:}"
    if [[ ! -d "$path" ]]; then
        echo "SKIP $tier ($path missing)"
        continue
    fi
    echo "── $tier ($path) ≥ ${floor}% ──"
    if ! coverage report --include="${path}/*" --fail-under="$floor"; then
        echo "FAIL $tier slipped below ${floor}%"
        FAILED=$((FAILED + 1))
    fi
done

if [[ "$FAILED" -gt 0 ]]; then
    echo "Coverage gates failed: ${FAILED} tier(s) below floor"
    exit 1
fi
echo "All coverage gates passed."

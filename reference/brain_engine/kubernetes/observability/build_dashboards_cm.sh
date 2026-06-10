#!/usr/bin/env bash
# Regenerate kubernetes/observability/21-grafana-dashboards.yaml from
# the source JSON files under infra/grafana/dashboards/.  Run this
# after editing any dashboard so the AKS ConfigMap stays in sync
# with the docker-compose-mounted files.
#
# Usage: ./kubernetes/observability/build_dashboards_cm.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

python3 <<'PY'
import json
import pathlib

src_dir = pathlib.Path("infra/grafana/dashboards")
files = sorted(src_dir.glob("*.json"))

lines = [
    "# Auto-generated from infra/grafana/dashboards/.",
    "# Re-run kubernetes/observability/build_dashboards_cm.sh after",
    "# editing the source JSONs to refresh this ConfigMap.",
    "---",
    "apiVersion: v1",
    "kind: ConfigMap",
    "metadata:",
    "  name: grafana-dashboards",
    "  namespace: observability",
    "data:",
]
for f in files:
    content = f.read_text()
    json.loads(content)  # validate
    indented = "\n".join("    " + ln for ln in content.splitlines())
    lines.append(f"  {f.name}: |")
    lines.append(indented)

out = pathlib.Path("kubernetes/observability/21-grafana-dashboards.yaml")
out.write_text("\n".join(lines) + "\n")
print(f"Wrote {out} with {len(files)} dashboards")
PY

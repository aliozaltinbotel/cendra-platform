"""Extract Brain Engine SQL migrations from the k8s manifest.

Reads ``deploy/postgres-migrations.yaml`` and writes each migration's
SQL body into ``infra/postgres-init/`` so a vanilla Postgres container
can ingest them via ``/docker-entrypoint-initdb.d``.

The k8s migrations are spread across many ``ConfigMap`` documents whose
``data`` map carries one key per ``NNN_<name>.sql`` file.  We preserve
the leading number so the alphabetical ordering Postgres uses to run
init scripts matches the migration sequence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "deploy" / "postgres-migrations.yaml"
DST = ROOT / "infra" / "postgres-init"


def main() -> int:
    if not SRC.exists():
        print(f"manifest not found: {SRC}", file=sys.stderr)
        return 1
    DST.mkdir(parents=True, exist_ok=True)
    for old in DST.glob("*.sql"):
        old.unlink()
    written = 0
    with SRC.open() as f:
        for doc in yaml.safe_load_all(f):
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") != "ConfigMap":
                continue
            data = doc.get("data") or {}
            for name, body in data.items():
                if not name.endswith(".sql"):
                    continue
                target = DST / name
                target.write_text(body)
                print(f"wrote {target.relative_to(ROOT)}")
                written += 1
    if written == 0:
        print("no migrations extracted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

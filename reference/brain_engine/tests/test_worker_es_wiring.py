"""Pin that the bootstrap worker wires the Elasticsearch property reader.

Under ``BOOTSTRAP_QUEUE_ENABLED`` the KEDA worker — not the API server —
is the real profile-harvest path.  ``build_worker_context`` must call
``wire_elasticsearch(holder)`` BEFORE ``wire_onboarding(holder, ...)`` so
the harvester it builds picks up the ES enrichment overlay; otherwise the
overlay only ever runs in the (idle) API process.
"""

from __future__ import annotations

import inspect

import workers.bootstrap_deps as deps


def test_worker_context_wires_elasticsearch_before_onboarding() -> None:
    src = inspect.getsource(deps.build_worker_context)
    assert "wire_elasticsearch(holder)" in src
    assert "wire_onboarding(" in src
    assert src.index("wire_elasticsearch(holder)") < src.index(
        "wire_onboarding(",
    )

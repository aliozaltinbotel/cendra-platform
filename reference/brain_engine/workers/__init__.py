"""Out-of-process workers for the Brain Engine (Stage 2+).

Each module here is a standalone ``python -m`` entrypoint that runs
*outside* the FastAPI serving process.  The first inhabitant is the
bootstrap worker (:mod:`workers.bootstrap_worker`), which drains the
``bootstrap-intents`` Service Bus queue produced by the Stage 2
dispatcher and runs the heavy ``bootstrap_fast`` pipeline where it
can no longer starve the request path.

Workers deliberately do **not** import the FastAPI app.  They
rebuild only the dependencies the bootstrap pipeline needs from the
same environment variables the server reads, so a worker pod never
starts the server's background jobs (orphan reaper, nightly
scheduler, auto-bootstrap trigger).  This isolation is the whole
point of running them as separate processes.
"""

from __future__ import annotations

__all__: list[str] = []

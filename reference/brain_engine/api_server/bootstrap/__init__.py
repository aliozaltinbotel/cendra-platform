"""Lifespan bootstrap modules for :mod:`api_server.server`.

Each module in this package owns the wiring of one subsystem that
``api_server.server.lifespan`` used to construct inline.  A module
exposes a single async ``wire`` entry point that builds its
resources, attaches load-bearing references to ``application.state``
for downstream readers, and returns the handles that ``lifespan``
must keep on its own stack frame so the matching shutdown branch can
release them.

The split exists to satisfy ``python_master_guide_2026.md`` section
4 (Single Responsibility) and section 14 (function complexity): the
original ``lifespan`` was a 1300-line god function that crashloops
were able to hide inside (incident 2026-04-25).  Pulling each
subsystem into its own module makes the surface inspectable and
gives every section a focused unit-test seam.

The migration is intentionally incremental.  Module-level globals in
``server.py`` are kept as the source of truth until every subsystem
has its own ``wire``; only then are readers migrated to
``request.app.state``.  Mixing the two refactors would put readers
at risk before the bootstrap path is stable.
"""

"""ContextVar plumbing for the active :class:`TenantContext`.

Downstream services read :func:`current_tenant` instead of accepting
a ``TenantContext`` argument on every method, which keeps existing
loader / reader signatures stable.  Setting and clearing the
ContextVar is the exclusive responsibility of
:class:`brain_engine.tenants.middleware.TenantResolverMiddleware` —
direct callers should never mutate it.

Two helpers are provided:

* :func:`current_tenant` — non-throwing accessor; returns ``None``
  when no middleware has bound a context to the current request
  (unit tests, background tasks, CLI entry points).
* :func:`bind_tenant` — context manager used internally by the
  middleware and by tests that want to simulate a request without
  spinning up FastAPI.  Pairs ``ContextVar.set`` with a guaranteed
  ``ContextVar.reset`` in ``__exit__``.

The ContextVar default is ``None`` so the existing single-tenant
code paths (pod env defaults) keep working byte-for-byte when no
middleware is installed — Phase 3 is fully opt-in at the runtime
level.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from brain_engine.tenants.models import TenantContext

__all__ = [
    "bind_tenant",
    "current_tenant",
]


_CURRENT_TENANT: ContextVar[TenantContext | None] = ContextVar(
    "brain_engine.current_tenant",
    default=None,
)


def current_tenant() -> TenantContext | None:
    """Return the :class:`TenantContext` bound to this request.

    Returns:
        The active context, or ``None`` when no middleware has set
        one (background workers, tests that bypass the resolver).
    """

    return _CURRENT_TENANT.get()


@contextmanager
def bind_tenant(context: TenantContext) -> Iterator[TenantContext]:
    """Bind ``context`` for the duration of the ``with`` block.

    Used by :class:`TenantResolverMiddleware` and by integration
    tests that want to assert downstream behaviour under a
    specific tenant without booting FastAPI.  Always resets the
    ContextVar on exit — ``copy_context()`` already isolates
    concurrent async tasks, but the explicit reset is defensive
    against single-threaded mis-nesting bugs.

    Args:
        context: The tenant to make active.

    Yields:
        The same ``context`` so callers can use the ``as`` clause.
    """

    token = _CURRENT_TENANT.set(context)
    try:
        yield context
    finally:
        _CURRENT_TENANT.reset(token)

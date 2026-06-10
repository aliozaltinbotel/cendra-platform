"""HTTP routers extracted from the api_server monolith.

The monolithic ``api_server/server.py`` historically owned every
route.  As we slice it into composable units, individual feature
areas land here.  The first inhabitant is the metrics exporter —
its surface is small, its dependencies are minimal, and the
production runbook needs it on day one.
"""

from api_server.routers.experiments import (
    configure_deps as configure_experiments_deps,
)
from api_server.routers.experiments import (
    router as experiments_router,
)
from api_server.routers.foundation_audit import (
    configure_deps as configure_foundation_audit_deps,
)
from api_server.routers.foundation_audit import (
    router as foundation_audit_router,
)
from api_server.routers.memory_smoke import (
    configure_deps as configure_memory_smoke_deps,
)
from api_server.routers.memory_smoke import (
    router as memory_smoke_router,
)
from api_server.routers.memory_status import (
    configure_deps as configure_memory_status_deps,
)
from api_server.routers.memory_status import (
    router as memory_status_router,
)
from api_server.routers.metrics import router as metrics_router
from api_server.routers.past_conversations import (
    configure_deps as configure_past_conversations_deps,
)
from api_server.routers.past_conversations import (
    router as past_conversations_router,
)

__all__ = [
    "configure_experiments_deps",
    "configure_foundation_audit_deps",
    "configure_memory_smoke_deps",
    "configure_memory_status_deps",
    "configure_past_conversations_deps",
    "experiments_router",
    "foundation_audit_router",
    "memory_smoke_router",
    "memory_status_router",
    "metrics_router",
    "past_conversations_router",
]

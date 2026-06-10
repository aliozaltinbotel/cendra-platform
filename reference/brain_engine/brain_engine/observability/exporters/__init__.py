"""Concrete exporters that ship engine metrics off-process.

The default backend is Prometheus (``prometheus_exporter``).  Other
backends (Datadog, OpenTelemetry, Sentry) are listed in advisory §5
and will be added on demand; they share the same metric vocabulary
defined in ``brain_engine.observability.metrics`` so a switch is a
single import change.
"""

from brain_engine.observability.exporters.prometheus_exporter import (
    PrometheusExporter,
    build_default_exporter,
)

__all__ = [
    "PrometheusExporter",
    "build_default_exporter",
]

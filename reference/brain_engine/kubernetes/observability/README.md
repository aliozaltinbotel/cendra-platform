# Brain Engine — Observability stack (AKS)

Self-contained Prometheus + Grafana + Langfuse stack that mirrors
the local `docker-compose.observability.yml` setup.  Apply once
and Mümin gets four ready-to-share dashboards plus a Langfuse
trace UI populated by every LLM round-trip the Brain Engine
performs.

## Files

| File | What |
|---|---|
| `00-namespace.yaml` | `observability` namespace |
| `10-prometheus.yaml` | Deployment + ConfigMap (kubernetes_sd_configs) + RBAC + Service |
| `20-grafana.yaml` | Deployment + datasource ConfigMap + admin Secret + Service |
| `21-grafana-dashboards.yaml` | ConfigMap holding the four dashboards (auto-generated from `infra/grafana/dashboards/`) |
| `30-langfuse.yaml` | StatefulSet langfuse-postgres + Deployment langfuse + Services |
| `build_dashboards_cm.sh` | Regenerate `21-grafana-dashboards.yaml` after editing source JSONs |

## Apply

```bash
kubectl apply -f kubernetes/observability/00-namespace.yaml
kubectl apply -f kubernetes/observability/10-prometheus.yaml
kubectl apply -f kubernetes/observability/20-grafana.yaml
kubectl apply -f kubernetes/observability/21-grafana-dashboards.yaml
kubectl apply -f kubernetes/observability/30-langfuse.yaml
```

The brain-engine pod template (in `kubernetes/brain-engine.yaml`)
already carries the `prometheus.io/scrape="true"` annotation that
the prometheus job picks up — no additional config required.

## Verify

```bash
# All four pods ready
kubectl -n observability get pods

# Prometheus discovered the brain-engine pod
kubectl -n observability port-forward svc/prometheus 9090:9090
# → open http://localhost:9090/targets, look for kubernetes-pods job

# Grafana shows the four dashboards
kubectl -n observability port-forward svc/grafana 3000:3000
# → open http://localhost:3000, login admin/admin
# → Dashboards → Brain Engine folder

# Langfuse UI
kubectl -n observability port-forward svc/langfuse 3001:3000
# → open http://localhost:3001
```

## Expose externally (optional)

Add an `Ingress` resource pointed at the `grafana` and
`langfuse` Services with TLS once Mümin's URLs are confirmed.
The Services themselves are `ClusterIP` so nothing leaks until an
explicit ingress is added.

## Update dashboards

Edit JSON files in `infra/grafana/dashboards/`, then:

```bash
./kubernetes/observability/build_dashboards_cm.sh
kubectl apply -f kubernetes/observability/21-grafana-dashboards.yaml
# Grafana picks up the new ConfigMap content within ~30s.
```

## Wire LLM traces into Langfuse

The brain-engine pod's `BaseChatModel.invoke` already calls
`get_default_tracer()`.  Set these env vars on the brain-engine
Deployment to start streaming traces:

```yaml
- name: LANGFUSE_PUBLIC_KEY
  value: pk-...   # generated in the Langfuse UI
- name: LANGFUSE_SECRET_KEY
  value: sk-...
- name: LANGFUSE_HOST
  value: http://langfuse.observability.svc.cluster.local:3000
```

Without the keys the tracer stays disabled (no-op spans) so the
manifest is safe to land before keys are issued.

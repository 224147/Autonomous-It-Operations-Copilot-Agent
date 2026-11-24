# Monitoring integrations

All ingestion endpoints live under `/api/v1/ingest/*` and require `X-API-Key: <one of INGEST_API_KEYS>` (the same key may be sent as `Authorization: Bearer <key>`) or an operator+ JWT.
Signals are held in a bounded recent window (`SIGNAL_RETENTION_PER_SERIES`, default 3000 per service/kind) in Redis; long-term
retention stays in your observability stack. Services are matched by (lower-cased) name against the inventory (`/api/v1/services`).

## Push endpoints

| Endpoint | Payload |
|---|---|
| `POST /ingest/logs` | `{"logs":[{"service","message","severity?","timestamp?","attributes?"}]}` |
| `POST /ingest/logs/loki` | Loki push format `{"streams":[{"stream":{labels},"values":[["<ns>","line"]]}]}` (service from `service`/`app`/`deployment`/`pod` labels) |
| `POST /ingest/metrics` | `{"metrics":[{"service","name","value","timestamp?"}]}` |
| `POST /ingest/metrics/otlp` | OTLP/HTTP **JSON** metrics (gauge + sum data points; `service.name` resource attribute) |
| `POST /ingest/traces` | `{"spans":[{"service","trace_id","span_id","name","duration_ms","status":"ok|error","peer_service?"}]}` |
| `POST /ingest/traces/otlp` | OTLP/HTTP **JSON** traces (`peer.service` / `server.address` attributes become the dependency edge) |
| `POST /ingest/alerts` | `[{"name","service","severity","status":"firing|resolved","summary","labels"}]` |
| `POST /ingest/alerts/alertmanager` | Prometheus Alertmanager / Grafana alerting webhook |
| `POST /ingest/alerts/azure-monitor` | Azure Monitor *common alert schema* webhook |
| `POST /ingest/alerts/datadog` | Datadog webhook (`tags` such as `service:orders`) |
| `POST /deployments` | `{"service","version","status","environment","commit_sha","deployed_by"}` - called from CI/CD; used by RCA to link regressions to releases |

OTLP/protobuf, Prometheus remote-write, Splunk and ELK are **not** ingested natively. Forward them through an OpenTelemetry
Collector (OTLP/JSON `otlphttp` exporter with `encoding: json`) or a webhook that maps to the generic endpoints above.

### Canonical metric names

Detection rules (`agents/common/taxonomy.py`) use: `cpu_usage_percent`, `memory_usage_percent`, `error_rate_percent`,
`latency_p99_ms`, `restart_count`, `disk_usage_percent`, `db_connection_utilization_percent`, `availability_percent`
(plus `request_rate` for traffic-surge detection). Aliases such as `cpu`, `memory`, `error_rate`, `latency_p99` are mapped.

### Alertmanager example

Webhook senders that can only set an `Authorization` header may present the ingest key as a bearer token:

```yaml
receivers:
  - name: copilot
    webhook_configs:
      - url: https://copilot.example.com/api/v1/ingest/alerts/alertmanager
        send_resolved: true
        http_config:
          authorization:
            type: Bearer
            credentials_file: /etc/alertmanager/copilot-ingest-key   # one of INGEST_API_KEYS
```

## Pull connectors (optional)

* `PROMETHEUS_URL` - the Monitoring Agent and the poller query `query_range` per service using templates in
  `monitoring/metric_ingestion/prometheus.py` (`$namespace`, `$deployment`, `$service` placeholders). Override with
  `PROMETHEUS_QUERIES_FILE` (JSON `{metric_name: promql}`) to match your metric names.
* `LOKI_URL` - error-oriented LogQL per service (`monitoring/log_ingestion/loki.py`).
* The poller runs every `POLL_INTERVAL_SECONDS` (leader-elected across replicas) and feeds the signal store so the detection
  sweep (`DETECTION_SWEEP_SECONDS`) sees them continuously.

## Incident trigger

`monitoring/alert_ingestion/service.py::IncidentTrigger` evaluates a service's scope (itself + dependencies + dependants)
when a firing alert arrives and on every sweep. It creates **one** incident per correlated failure (de-duplicated against
active incidents, and against recently resolved incidents unless anomalies are newer than the resolution) and queues the agent workflow.

## Not integrated (out of the box)

Splunk, ELK/OpenSearch, Datadog metric/log pull, Azure Monitor pull, GCP/AWS CloudWatch pull. Add a connector implementing
`agents.common.ports.SignalConnector` (`fetch(service, since, until) -> {logs, metrics, traces}`) and register it in
`backend/container.py`.

"""Prometheus metrics for the platform itself (agent latency, RCA accuracy, alert processing, API latency...)."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

_LAT = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)

HTTP_REQUESTS = Counter("copilot_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram("copilot_http_request_duration_seconds", "API latency", ["method", "route"], buckets=_LAT, registry=REGISTRY)
AGENT_LATENCY = Histogram("copilot_agent_duration_seconds", "Agent execution latency", ["agent"], buckets=_LAT, registry=REGISTRY)
AGENT_ERRORS = Counter("copilot_agent_errors_total", "Agent failures", ["agent"], registry=REGISTRY)
WORKFLOW_RUNS = Counter("copilot_workflow_runs_total", "Workflow runs by final status", ["status"], registry=REGISTRY)
ALERT_PROCESSING = Histogram("copilot_alert_processing_seconds", "Time from alert receipt to incident workflow completion", buckets=_LAT, registry=REGISTRY)
ALERTS_RECEIVED = Counter("copilot_alerts_received_total", "Alerts ingested", ["source"], registry=REGISTRY)
SIGNALS_INGESTED = Counter("copilot_signals_ingested_total", "Signals ingested", ["kind"], registry=REGISTRY)
INCIDENTS_CREATED = Counter("copilot_incidents_created_total", "Incidents created", ["severity"], registry=REGISTRY)
INCIDENTS_OPEN = Gauge("copilot_incidents_open", "Currently open incidents", registry=REGISTRY)
INCIDENT_RESOLUTION = Histogram(
    "copilot_incident_resolution_seconds", "Incident time to resolution", buckets=(60, 300, 900, 1800, 3600, 7200, 14400, 86400), registry=REGISTRY
)
REMEDIATIONS = Counter("copilot_remediation_actions_total", "Remediation actions", ["action", "status"], registry=REGISTRY)
RCA_FEEDBACK = Counter("copilot_rca_feedback_total", "RCA feedback", ["result"], registry=REGISTRY)
RCA_ACCURACY = Gauge("copilot_rca_accuracy_ratio", "Share of RCA reports confirmed correct by responders", registry=REGISTRY)
QUEUE_DEPTH = Gauge("copilot_workflow_queue_depth", "Pending workflow jobs", registry=REGISTRY)
APP_UP = Gauge("copilot_up", "1 when the application is running", registry=REGISTRY)

export type Role = "viewer" | "operator" | "approver" | "admin";
export const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, approver: 2, admin: 3 };

export type SeverityLevel = "P1" | "P2" | "P3" | "P4";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  auth_provider: string;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthConfig {
  local_login: boolean;
  entra_enabled: boolean;
  entra_tenant_id: string | null;
  entra_client_id: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Incident {
  id: number;
  title: string;
  service: string;
  affected_services: string[];
  category: string;
  severity: SeverityLevel;
  status: string;
  summary: string;
  workflow_status: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface Evidence {
  type: string;
  service?: string;
  detail: string;
  timestamp?: string | null;
  severity?: string;
  sample?: string;
}

export interface TimelineItem {
  timestamp: string;
  source?: string;
  type?: string;
  service?: string | null;
  stage?: string;
  severity?: string;
  description: string;
}

export interface RcaReport {
  id: number;
  incident_id: number;
  root_cause: string;
  category: string;
  confidence: number;
  explanation: string;
  contributing_factors: string[];
  evidence: Evidence[];
  timeline: TimelineItem[];
  recommendations: PlanAction[];
  feedback: "correct" | "incorrect" | null;
  feedback_notes: string;
  created_at: string;
}

export interface PlanAction {
  id: string;
  rank?: number;
  action: string;
  target: string;
  params?: Record<string, unknown>;
  risk: "low" | "medium" | "high";
  score?: number;
  rationale?: string;
  description?: string;
}

export interface Approval {
  id: number;
  incident_id: number | null;
  plan: PlanAction[];
  approved_actions: string[];
  status: "pending" | "approved" | "rejected" | "expired";
  risk: string;
  requested_at: string;
  expires_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  comment: string;
  auto_approved: boolean;
}

export interface Execution {
  id: number;
  incident_id: number | null;
  approval_id: number | null;
  action: string;
  target: string;
  params: Record<string, unknown>;
  status: string;
  mode: string;
  output: Record<string, unknown>;
  error: string;
  executed_by: string;
  started_at: string;
  finished_at: string | null;
}

export interface Ticket {
  id: number;
  incident_id: number | null;
  title: string;
  description: string;
  priority: string;
  status: string;
  external_ref: string;
  created_at: string;
}

export interface WorkflowEvent {
  timestamp: string;
  stage: string;
  message: string;
}

export interface IncidentDetail extends Incident {
  detection: { anomalies?: Array<{ description: string; severity: string; service: string; type: string }> };
  correlation: {
    propagation_paths?: string[][];
    root_candidates?: Array<{ service: string; monitored: boolean }>;
    timeline?: TimelineItem[];
    recent_deployments?: Array<{ service: string; version: string; deployed_at: string; minutes_before_anomaly?: number }>;
  };
  recommendations: PlanAction[];
  resolution_notes: string;
  rca: RcaReport | null;
  approvals: Approval[];
  executions: Execution[];
  tickets: Ticket[];
  events: WorkflowEvent[];
  verification: { status?: string; details?: string };
}

export interface Alert {
  id: number;
  fingerprint: string;
  source: string;
  name: string;
  service: string;
  severity: string;
  status: "firing" | "resolved" | "acknowledged";
  summary: string;
  labels: Record<string, unknown>;
  starts_at: string;
  ends_at: string | null;
  received_at: string;
  acknowledged_by: string | null;
  incident_id: number | null;
}

export interface ServiceHealth {
  name: string;
  tier: number;
  team: string;
  kind: string;
  state: "healthy" | "degraded" | "critical" | "unknown";
  metrics: Record<string, number>;
  anomalies: Array<{ name: string; severity: string; description: string }>;
  active_incidents: Array<{ id: number; severity: string; title: string }>;
  depends_on: string[];
}

export interface Deployment {
  id: number;
  service: string;
  version: string;
  image: string;
  environment: string;
  status: string;
  commit_sha: string;
  deployed_by: string;
  source: string;
  notes: string;
  deployed_at: string;
}

export interface IncidentStats {
  window_days: number;
  total_incidents: number;
  active_incidents: number;
  resolved_incidents: number;
  mttr_seconds: number | null;
  by_severity: Record<string, number>;
  by_category: Record<string, number>;
  by_service: Record<string, number>;
  by_status: Record<string, number>;
  trend: Array<{ date: string; P1: number; P2: number; P3: number; P4: number; total: number }>;
  alerts_received: number;
  remediation_actions: number;
  remediation_success_rate: number | null;
  automated_remediation_coverage: number | null;
  rca_accuracy: number | null;
  rca_feedback_count: number;
}

export interface Kpis {
  window_days: number;
  mttr_seconds: number | null;
  rca_accuracy: number | null;
  rca_feedback_count: number;
  automated_remediation_coverage: number | null;
  remediation_success_rate: number | null;
  total_incidents: number;
  targets: Record<string, number>;
  notes: Record<string, string>;
}

export interface ChatSource {
  type: string;
  id: string | number;
  title?: string;
}

export interface ChatResponse {
  session_id: string;
  answer: string;
  sources: ChatSource[];
  suggested_actions: string[];
  llm_used: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  suggested_actions?: string[];
}

export interface AuditEntry {
  id: number;
  timestamp: string;
  actor: string;
  actor_role: string;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  details: Record<string, unknown>;
  ip_address: string;
  hash: string;
}

export interface KnowledgeHit {
  id: string;
  text: string;
  score: number;
  metadata: Record<string, string | number | boolean>;
}

export interface LiveEvent {
  id: string;
  name: string;
  at: string;
  payload: Record<string, unknown>;
}

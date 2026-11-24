import { api } from "./client";
import type {
  Alert, Approval, AuditEntry, AuthConfig, ChatMessage, ChatResponse, Deployment, Execution, Incident, IncidentDetail,
  IncidentStats, KnowledgeHit, Kpis, Page, RcaReport, ServiceHealth, Ticket, User,
} from "../types";

const get = async <T>(url: string, params?: object): Promise<T> => (await api.get<T>(url, { params })).data;

export const authApi = {
  config: () => get<AuthConfig>("/auth/config"),
  me: () => get<User>("/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    api.post("/auth/change-password", { current_password, new_password }),
  logout: (refresh_token: string | null) => api.post("/auth/logout", refresh_token ? { refresh_token } : undefined),
};

export const incidentsApi = {
  list: (params: { status?: string; severity?: string; service?: string; active?: boolean; limit?: number; offset?: number }) =>
    get<Page<Incident>>("/incidents", params),
  get: (id: number) => get<IncidentDetail>(`/incidents/${id}`),
  stats: (days = 30) => get<IncidentStats>("/incidents/stats", { days }),
  create: (body: { service: string; title?: string; severity?: string; description?: string }) =>
    api.post<Incident>("/incidents", body).then((r) => r.data),
  analyze: (id: number) => api.post(`/incidents/${id}/analyze`),
  resolve: (id: number, notes: string) => api.post<Incident>(`/incidents/${id}/resolve`, { notes }).then((r) => r.data),
  setStatus: (id: number, status: string) => api.patch<Incident>(`/incidents/${id}/status`, { status }).then((r) => r.data),
  report: (id: number) => get<{ markdown?: string; mttr_seconds?: number }>(`/reporting/incidents/${id}`),
};

export const rcaApi = {
  list: (limit = 50) => get<Page<RcaReport>>("/rca", { limit }),
  feedback: (id: number, result: "correct" | "incorrect", notes = "") =>
    api.post<RcaReport>(`/rca/${id}/feedback`, { result, notes }).then((r) => r.data),
  accuracy: () => get<{ feedback_count: number; correct: number; accuracy: number | null }>("/rca/accuracy"),
};

export const remediationApi = {
  approvals: (status?: string) => get<Approval[]>("/remediation/approvals", status ? { status } : undefined),
  decide: (id: number, decision: "approve" | "reject", comment: string, approved_actions: string[]) =>
    api.post<Approval>(`/remediation/approvals/${id}/decision`, { decision, comment, approved_actions }).then((r) => r.data),
  executions: (limit = 100) => get<Execution[]>("/remediation/executions", { limit }),
  tickets: (limit = 100) => get<Ticket[]>("/remediation/tickets", { limit }),
  catalogue: () => get<{ mode: string; actions: Array<{ name: string; risk: string; description: string }> }>("/remediation/actions"),
};

export const monitoringApi = {
  alerts: (params: { status?: string; severity?: string; service?: string; limit?: number; offset?: number }) =>
    get<Page<Alert>>("/alerts", params),
  acknowledge: (id: number) => api.post<Alert>(`/alerts/${id}/acknowledge`).then((r) => r.data),
  resolve: (id: number) => api.post<Alert>(`/alerts/${id}/resolve`).then((r) => r.data),
  serviceHealth: () => get<ServiceHealth[]>("/services/health"),
  deployments: (service?: string) => get<Deployment[]>("/deployments", { service, limit: 100 }),
};

export const reportingApi = {
  kpis: (days = 30) => get<Kpis>("/reporting/kpis", { days }),
};

export const knowledgeApi = {
  search: (query: string, mode: "hybrid" | "semantic" | "keyword" = "hybrid") =>
    api.post<{ results: KnowledgeHit[] }>("/knowledge/search", { query, mode, k: 8 }).then((r) => r.data.results),
};

export const chatApi = {
  send: (message: string, session_id?: string) => api.post<ChatResponse>("/chat", { message, session_id }).then((r) => r.data),
  history: (session_id: string) => get<ChatMessage[]>(`/chat/${session_id}`),
};

export const adminApi = {
  users: () => get<User[]>("/users"),
  createUser: (body: { email: string; full_name: string; password: string; role: string }) =>
    api.post<User>("/users", body).then((r) => r.data),
  updateUser: (id: number, body: Partial<{ full_name: string; role: string; is_active: boolean }>) =>
    api.patch<User>(`/users/${id}`, body).then((r) => r.data),
  audit: (params: { limit: number; offset: number; actor?: string; action?: string }) =>
    get<{ items: AuditEntry[]; total: number }>("/audit", params),
  verifyAudit: () => get<{ valid: boolean; checked: number; broken_at: number | null }>("/audit/verify"),
};

export interface KnowledgeDocSummary {
  id: string;
  metadata: Record<string, string | number | boolean>;
}

export const knowledgeDocsApi = {
  list: (doc_type?: string) => get<KnowledgeDocSummary[]>("/knowledge/documents", doc_type ? { doc_type } : undefined),
  get: (id: string) => get<{ id: string; text: string; metadata: Record<string, string | number | boolean> }>(`/knowledge/documents/${encodeURIComponent(id)}`),
  stats: () => get<{ chunks: number; collection: string; embedder: string }>("/knowledge/stats"),
};

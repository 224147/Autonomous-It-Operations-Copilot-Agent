import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from "axios";

import type { TokenPair } from "../types";

/**
 * Auth wiring is injected (see store/index.ts) so the HTTP layer never imports the store (no circular deps).
 * The access token lives in memory (Redux); the refresh token in sessionStorage: a deliberate trade-off that survives
 * page reloads but not browser restarts. An XSS bug could read it - the production NGINX CSP forbids inline scripts.
 */
export interface AuthBridge {
  getAccessToken: () => string | null;
  getRefreshToken: () => string | null;
  onTokens: (tokens: TokenPair) => void;
  onLogout: () => void;
}

export const REFRESH_KEY = "copilot.refresh";

let bridge: AuthBridge | null = null;
let refreshing: Promise<string | null> | null = null;

export function configureAuthBridge(next: AuthBridge): void {
  bridge = next;
}

export const baseURL = `${import.meta.env.VITE_API_URL ?? ""}/api/v1`;

export const api: AxiosInstance = axios.create({ baseURL, timeout: 30_000 });
export const plain: AxiosInstance = axios.create({ baseURL, timeout: 30_000 }); // no interceptors (login/refresh)

/** Single-flight refresh: concurrent 401s share one refresh request (refresh tokens rotate and are single-use). */
export function refreshAccessToken(): Promise<string | null> {
  if (refreshing) return refreshing;
  const refreshToken = bridge?.getRefreshToken();
  if (!refreshToken) return Promise.resolve(null);
  refreshing = plain
    .post<TokenPair>("/auth/refresh", { refresh_token: refreshToken })
    .then((resp) => {
      bridge?.onTokens(resp.data);
      return resp.data.access_token;
    })
    .catch(() => null)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = bridge?.getAccessToken();
  if (token) config.headers.set("Authorization", `Bearer ${token}`);
  return config;
});

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as RetriableConfig | undefined;
    if (error.response?.status === 401 && original && !original._retried) {
      original._retried = true;
      const token = await refreshAccessToken();
      if (token) {
        original.headers.set("Authorization", `Bearer ${token}`);
        return api.request(original);
      }
      bridge?.onLogout();
    }
    return Promise.reject(error);
  },
);

export async function loginRequest(email: string, password: string): Promise<TokenPair> {
  return (await plain.post<TokenPair>("/auth/login", { email, password })).data;
}

export async function entraLoginRequest(idToken: string): Promise<TokenPair> {
  return (await plain.post<TokenPair>("/auth/sso/entra", { id_token: idToken })).data;
}

export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg ?? "invalid input").join("; ");
    if (err.response?.status === 403) return "You do not have permission to perform this action.";
    if (err.code === "ECONNABORTED") return "The request timed out.";
    return err.message;
  }
  return err instanceof Error ? err.message : "Unexpected error";
}

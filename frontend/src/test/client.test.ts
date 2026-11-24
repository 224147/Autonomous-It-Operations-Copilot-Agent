import type { AxiosAdapter, InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, configureAuthBridge, errorMessage, plain } from "../api/client";

const reply = (config: InternalAxiosRequestConfig, status: number, data: unknown) => {
  const response = { status, statusText: "", headers: {}, config, request: {}, data };
  if (status >= 400) throw Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, response, config });
  return response;
};

describe("api client token refresh", () => {
  let tokens = { access: "old", refresh: "r1" };
  let apiCalls: string[] = [];
  let refreshCalls = 0;
  const onLogout = vi.fn();

  beforeEach(() => {
    tokens = { access: "old", refresh: "r1" };
    apiCalls = [];
    refreshCalls = 0;
    onLogout.mockClear();
    configureAuthBridge({
      getAccessToken: () => tokens.access,
      getRefreshToken: () => tokens.refresh,
      onTokens: (t) => { tokens = { access: t.access_token, refresh: t.refresh_token }; },
      onLogout,
    });
  });
  afterEach(() => sessionStorage.clear());

  const refreshOk: AxiosAdapter = async (config) => {
    refreshCalls += 1;
    return reply(config, 200, { access_token: "new", refresh_token: "r2", token_type: "bearer", expires_in: 1800 });
  };

  it("retries once with a fresh token after a 401", async () => {
    api.defaults.adapter = async (config) => {
      apiCalls.push(String(config.headers.get("Authorization")));
      return config.headers.get("Authorization") === "Bearer old" ? reply(config, 401, {}) : reply(config, 200, { ok: true });
    };
    plain.defaults.adapter = refreshOk;
    const result = await api.get("/incidents");
    expect(result.data).toEqual({ ok: true });
    expect(apiCalls).toEqual(["Bearer old", "Bearer new"]);
    expect(tokens).toEqual({ access: "new", refresh: "r2" });
    expect(onLogout).not.toHaveBeenCalled();
  });

  it("shares one refresh request between concurrent 401s (refresh tokens are single-use)", async () => {
    api.defaults.adapter = async (config) =>
      config.headers.get("Authorization") === "Bearer old" ? reply(config, 401, {}) : reply(config, 200, { ok: true });
    plain.defaults.adapter = refreshOk;
    await Promise.all([api.get("/a"), api.get("/b"), api.get("/c")]);
    expect(refreshCalls).toBe(1);
  });

  it("logs out when the refresh fails", async () => {
    api.defaults.adapter = async (config) => reply(config, 401, {});
    plain.defaults.adapter = async (config) => reply(config, 401, {});
    await expect(api.get("/incidents")).rejects.toBeTruthy();
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("does not loop forever when the retried request is still 401", async () => {
    let attempts = 0;
    api.defaults.adapter = async (config) => { attempts += 1; return reply(config, 401, {}); };
    plain.defaults.adapter = refreshOk;
    await expect(api.get("/incidents")).rejects.toBeTruthy();
    expect(attempts).toBe(2);
  });
});

describe("errorMessage", () => {
  it("falls back gracefully for non-HTTP errors", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
    expect(errorMessage("x")).toBe("Unexpected error");
  });
});

import { configureStore } from "@reduxjs/toolkit";
import { render, screen } from "@testing-library/react";
import { Provider } from "react-redux";
import { afterEach, describe, expect, it } from "vitest";

import { ConfidenceGauge } from "../components/ConfidenceGauge";
import { RiskChip, SeverityChip } from "../components/Chips";
import { RcaTimeline } from "../incidents/RcaTimeline";
import authReducer, { loggedOut, tokensUpdated } from "../store/authSlice";
import uiReducer, { eventReceived, toastDismissed, toastPushed } from "../store/uiSlice";
import { formatDuration, humanize, percent, timeAgo } from "../utils/format";
import { useRole } from "../hooks/useRole";

const makeStore = () => configureStore({ reducer: { auth: authReducer, ui: uiReducer } });

afterEach(() => sessionStorage.clear());

describe("auth slice", () => {
  it("stores the access token and clears everything on logout", () => {
    const store = makeStore();
    store.dispatch(tokensUpdated("abc"));
    expect(store.getState().auth.accessToken).toBe("abc");
    sessionStorage.setItem("copilot.refresh", "r");
    store.dispatch(loggedOut());
    expect(store.getState().auth.accessToken).toBeNull();
    expect(store.getState().auth.status).toBe("anonymous");
    expect(sessionStorage.getItem("copilot.refresh")).toBeNull();
  });
});

describe("ui slice", () => {
  it("caps the live feed and toast list", () => {
    const store = makeStore();
    for (let i = 0; i < 80; i++) store.dispatch(eventReceived({ id: String(i), name: "x", at: "", payload: {} }));
    expect(store.getState().ui.feed).toHaveLength(60);
    for (let i = 0; i < 5; i++) store.dispatch(toastPushed({ id: `t${i}`, severity: "info", message: "m" }));
    expect(store.getState().ui.toasts).toHaveLength(3);
    store.dispatch(toastDismissed("t4"));
    expect(store.getState().ui.toasts.map((t) => t.id)).toEqual(["t2", "t3"]);
  });
});

describe("formatting helpers", () => {
  it("formats durations, percentages and labels", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(600)).toBe("10m");
    expect(formatDuration(null)).toBe("-");
    expect(percent(0.857)).toBe("86%");
    expect(percent(null)).toBe("-");
    expect(humanize("memory_exhaustion")).toBe("Memory exhaustion");
    expect(timeAgo(new Date(Date.now() - 5 * 60_000).toISOString())).toMatch(/5 minutes ago/);
  });
});

describe("components", () => {
  it("renders severity/risk chips and the confidence gauge accessibly", () => {
    render(<><SeverityChip severity="P1" /><RiskChip risk="high" /><ConfidenceGauge value={0.874} /></>);
    expect(screen.getByText("P1")).toBeInTheDocument();
    expect(screen.getByText("high risk")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Confidence 87%" })).toBeInTheDocument();
  });

  it("renders timeline items and an empty state", () => {
    const { rerender } = render(<RcaTimeline items={[{ timestamp: "2026-01-01T10:00:00Z", description: "OOMKilled detected", service: "pay", type: "log" }]} />);
    expect(screen.getByText("OOMKilled detected")).toBeInTheDocument();
    rerender(<RcaTimeline items={[]} />);
    expect(screen.getByText(/No timeline/)).toBeInTheDocument();
  });

  it("escapes HTML from untrusted text instead of rendering it", () => {
    render(<RcaTimeline items={[{ timestamp: "2026-01-01T10:00:00Z", description: "<img src=x onerror=alert(1)>" }]} />);
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("role hook", () => {
  it("reports role capabilities from the store", () => {
    const store = makeStore();
    function Probe() {
      const { has } = useRole();
      return <span>{has("approver") ? "can-approve" : "viewer-only"}</span>;
    }
    render(<Provider store={store}><Probe /></Provider>);
    expect(screen.getByText("viewer-only")).toBeInTheDocument();
  });
});

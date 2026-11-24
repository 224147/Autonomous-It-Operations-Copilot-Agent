import { io, type Socket } from "socket.io-client";

export const LIVE_EVENTS = [
  "incident.created", "incident.updated", "alert.received", "alert.updated", "approval.requested", "approval.decided",
  "remediation.executed", "ticket.created", "deployment.recorded",
] as const;
export type LiveEventName = (typeof LIVE_EVENTS)[number];

export function connectSocket(token: string, onEvent: (name: LiveEventName, payload: Record<string, unknown>) => void): Socket {
  const url = import.meta.env.VITE_WS_URL || window.location.origin;
  const socket = io(url, { path: "/ws/socket.io", auth: { token }, transports: ["websocket", "polling"], reconnectionDelayMax: 10_000 });
  for (const name of LIVE_EVENTS) socket.on(name, (payload: Record<string, unknown>) => onEvent(name, payload ?? {}));
  return socket;
}

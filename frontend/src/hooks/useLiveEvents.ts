import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { connectSocket, type LiveEventName } from "../api/socket";
import { eventReceived, socketStatus, toastPushed } from "../store/uiSlice";
import { useAppDispatch, useAppSelector } from "../store";

// which cached queries each real-time event makes stale
const INVALIDATIONS: Record<LiveEventName, string[]> = {
  "incident.created": ["incidents", "incident", "stats", "kpis", "health"],
  "incident.updated": ["incidents", "incident", "stats", "kpis", "health"],
  "alert.received": ["alerts", "health"],
  "alert.updated": ["alerts"],
  "approval.requested": ["approvals", "incident"],
  "approval.decided": ["approvals", "incident", "executions"],
  "remediation.executed": ["executions", "incident", "tickets"],
  "ticket.created": ["tickets", "incident"],
  "deployment.recorded": ["deployments"],
};

let counter = 0;

/** Maintains the Socket.IO connection (re-created whenever the access token changes) and fans events out. */
export function useLiveEvents(): void {
  const token = useAppSelector((s) => s.auth.accessToken);
  const dispatch = useAppDispatch();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!token) return undefined;
    const socket = connectSocket(token, (name, payload) => {
      counter += 1;
      dispatch(eventReceived({ id: `${Date.now()}-${counter}`, name, at: new Date().toISOString(), payload }));
      for (const key of INVALIDATIONS[name]) void queryClient.invalidateQueries({ queryKey: [key] });
      if (name === "incident.created") {
        dispatch(toastPushed({ id: `t${counter}`, severity: "warning", message: `New incident: ${String(payload.title ?? payload.id)}` }));
      } else if (name === "approval.requested") {
        dispatch(toastPushed({ id: `t${counter}`, severity: "info", message: "A remediation plan is awaiting approval" }));
      }
    });
    socket.on("connect", () => dispatch(socketStatus(true)));
    socket.on("disconnect", () => dispatch(socketStatus(false)));
    return () => {
      socket.removeAllListeners();
      socket.disconnect();
      dispatch(socketStatus(false));
    };
  }, [token, dispatch, queryClient]);
}

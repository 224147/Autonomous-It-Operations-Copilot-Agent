import { Chip, type ChipProps } from "@mui/material";

import type { SeverityLevel } from "../types";
import { humanize } from "../utils/format";

const SEVERITY_COLOR: Record<SeverityLevel, ChipProps["color"]> = { P1: "error", P2: "warning", P3: "info", P4: "default" };

export function SeverityChip({ severity }: { severity: string }) {
  return <Chip size="small" label={severity} color={SEVERITY_COLOR[severity as SeverityLevel] ?? "default"} sx={{ fontWeight: 700 }} />;
}

const STATUS_COLOR: Record<string, ChipProps["color"]> = {
  open: "error", investigating: "warning", awaiting_approval: "warning", remediating: "info", monitoring: "info",
  escalated: "error", resolved: "success", closed: "default", false_positive: "default",
  firing: "error", acknowledged: "warning", pending: "warning", approved: "success", rejected: "error", expired: "default",
  healthy: "success", degraded: "warning", critical: "error", unknown: "default",
  succeeded: "success", simulated: "info", failed: "error",
};

export function StatusChip({ status }: { status: string }) {
  return <Chip size="small" variant="outlined" label={humanize(status)} color={STATUS_COLOR[status] ?? "default"} />;
}

export function RiskChip({ risk }: { risk: string }) {
  const color: ChipProps["color"] = risk === "high" ? "error" : risk === "medium" ? "warning" : "success";
  return <Chip size="small" label={`${risk} risk`} color={color} variant="outlined" />;
}

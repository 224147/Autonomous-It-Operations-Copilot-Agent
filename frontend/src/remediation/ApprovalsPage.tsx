import { Alert, Chip, Paper, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { remediationApi } from "../api/endpoints";
import { StatusChip } from "../components/Chips";
import { EmptyState, ErrorAlert, Loading, PageHeader } from "../components/Common";
import { formatDateTime, humanize } from "../utils/format";
import { ApprovalCard } from "./ApprovalCard";

export function ApprovalsPage() {
  const [tab, setTab] = useState(0);
  const approvals = useQuery({ queryKey: ["approvals", "all"], queryFn: () => remediationApi.approvals() });
  const executions = useQuery({ queryKey: ["executions"], queryFn: () => remediationApi.executions(100) });
  const catalogue = useQuery({ queryKey: ["catalogue"], queryFn: remediationApi.catalogue, staleTime: 10 * 60_000 });

  const pending = approvals.data?.filter((a) => a.status === "pending") ?? [];
  const history = approvals.data?.filter((a) => a.status !== "pending") ?? [];
  const mode = catalogue.data?.mode;

  return (
    <>
      <PageHeader title="Approval workflow" subtitle="Human decisions gate every impactful remediation action"
        actions={mode ? <Chip color={mode === "live" ? "error" : "info"} label={mode === "live" ? "LIVE mode: actions change real infrastructure" : "dry-run mode: actions are simulated"} /> : undefined} />
      <ErrorAlert error={approvals.error ?? executions.error} />
      <Tabs value={tab} onChange={(_, v: number) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label={`Pending (${pending.length})`} /><Tab label="History" /><Tab label="Execution log" />
      </Tabs>
      {approvals.isLoading ? <Loading /> : (
        <>
          {tab === 0 && (pending.length === 0 ? <EmptyState message="Nothing is waiting for approval." /> : pending.map((a) => (
            <div key={a.id}>
              {a.incident_id && <Typography variant="caption" color="text.secondary">Incident INC-{a.incident_id}</Typography>}
              <ApprovalCard approval={a} />
            </div>
          )))}
          {tab === 1 && (history.length === 0 ? <EmptyState message="No decisions yet." /> : history.map((a) => <ApprovalCard key={a.id} approval={a} />))}
          {tab === 2 && (
            (executions.data?.length ?? 0) === 0 ? <EmptyState message="No remediation executions yet." /> : (
              <Paper variant="outlined" sx={{ overflowX: "auto" }}>
                <Table size="small" aria-label="Executions">
                  <TableHead><TableRow><TableCell>Time</TableCell><TableCell>Action</TableCell><TableCell>Target</TableCell><TableCell>Incident</TableCell><TableCell>Mode</TableCell><TableCell>Status</TableCell><TableCell>Detail</TableCell></TableRow></TableHead>
                  <TableBody>
                    {executions.data?.map((e) => (
                      <TableRow key={e.id}>
                        <TableCell>{formatDateTime(e.started_at)}</TableCell><TableCell>{humanize(e.action)}</TableCell><TableCell>{e.target}</TableCell>
                        <TableCell>{e.incident_id ? `INC-${e.incident_id}` : "-"}</TableCell>
                        <TableCell><Chip size="small" variant="outlined" color={e.mode === "live" ? "error" : "default"} label={e.mode} /></TableCell>
                        <TableCell><StatusChip status={e.status} /></TableCell>
                        <TableCell sx={{ maxWidth: 320 }}>{e.error || (e.output && Object.keys(e.output).length ? JSON.stringify(e.output).slice(0, 120) : "")}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Paper>
            )
          )}
        </>
      )}
      <Stack sx={{ mt: 2 }}><Alert severity="info">Approvers can approve a subset of a plan; the engine re-verifies every action against the approved plan before executing it.</Alert></Stack>
    </>
  );
}

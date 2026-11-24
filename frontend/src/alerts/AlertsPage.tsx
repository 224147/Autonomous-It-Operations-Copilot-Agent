import { Button, Link, MenuItem, Pagination, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { monitoringApi } from "../api/endpoints";
import { SeverityChip, StatusChip } from "../components/Chips";
import { EmptyState, ErrorAlert, Loading, PageHeader } from "../components/Common";
import { useRole } from "../hooks/useRole";
import { timeAgo } from "../utils/format";

const PAGE = 25;

export function AlertsPage() {
  const { has } = useRole();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("firing");
  const [severity, setSeverity] = useState("");
  const [service, setService] = useState("");
  const [page, setPage] = useState(1);

  const alerts = useQuery({
    queryKey: ["alerts", { status, severity, service, page }],
    queryFn: () => monitoringApi.alerts({ status: status || undefined, severity: severity || undefined, service: service.trim().toLowerCase() || undefined, limit: PAGE, offset: (page - 1) * PAGE }),
  });
  const act = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: "ack" | "resolve" }) => (kind === "ack" ? monitoringApi.acknowledge(id) : monitoringApi.resolve(id)),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });

  return (
    <>
      <PageHeader title="Alert center" subtitle="Aggregated, de-duplicated alerts from all monitoring sources" />
      <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ mb: 2 }}>
        <TextField select size="small" label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} sx={{ minWidth: 160 }}>
          {["", "firing", "acknowledged", "resolved"].map((s) => <MenuItem key={s} value={s}>{s || "Any"}</MenuItem>)}
        </TextField>
        <TextField select size="small" label="Severity" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }} sx={{ minWidth: 160 }}>
          {["", "critical", "error", "warning", "info"].map((s) => <MenuItem key={s} value={s}>{s || "Any"}</MenuItem>)}
        </TextField>
        <TextField size="small" label="Service" value={service} onChange={(e) => { setService(e.target.value); setPage(1); }} />
      </Stack>
      <ErrorAlert error={alerts.error ?? act.error} />
      {alerts.isLoading ? <Loading /> : (alerts.data?.items.length ?? 0) === 0 ? <EmptyState message="No alerts match." /> : (
        <Paper variant="outlined" sx={{ overflowX: "auto" }}>
          <Table size="small" aria-label="Alerts">
            <TableHead><TableRow><TableCell>Received</TableCell><TableCell>Severity</TableCell><TableCell>Alert</TableCell><TableCell>Service</TableCell><TableCell>Source</TableCell><TableCell>Status</TableCell><TableCell>Incident</TableCell><TableCell /></TableRow></TableHead>
            <TableBody>
              {alerts.data?.items.map((a) => (
                <TableRow key={a.id} hover>
                  <TableCell>{timeAgo(a.received_at)}</TableCell>
                  <TableCell><SeverityChip severity={a.severity} /></TableCell>
                  <TableCell>{a.name}<br /><small>{a.summary}</small></TableCell>
                  <TableCell>{a.service || "-"}</TableCell>
                  <TableCell>{a.source}</TableCell>
                  <TableCell><StatusChip status={a.status} /></TableCell>
                  <TableCell>{a.incident_id ? <Link component={RouterLink} to={`/incidents/${a.incident_id}`}>INC-{a.incident_id}</Link> : "-"}</TableCell>
                  <TableCell>
                    {has("operator") && a.status === "firing" && <Button size="small" onClick={() => act.mutate({ id: a.id, kind: "ack" })}>Acknowledge</Button>}
                    {has("operator") && a.status !== "resolved" && <Button size="small" onClick={() => act.mutate({ id: a.id, kind: "resolve" })}>Resolve</Button>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}
      {(alerts.data?.total ?? 0) > PAGE && <Pagination sx={{ mt: 2 }} count={Math.ceil((alerts.data?.total ?? 0) / PAGE)} page={page} onChange={(_, p) => setPage(p)} />}
    </>
  );
}

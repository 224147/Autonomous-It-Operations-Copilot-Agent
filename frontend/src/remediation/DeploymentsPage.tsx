import { Link, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { incidentsApi, monitoringApi } from "../api/endpoints";
import { StatusChip } from "../components/Chips";
import { EmptyState, ErrorAlert, Loading, PageHeader } from "../components/Common";
import { formatDateTime, timeAgo } from "../utils/format";

export function DeploymentsPage() {
  const [service, setService] = useState("");
  const deployments = useQuery({ queryKey: ["deployments", service], queryFn: () => monitoringApi.deployments(service.trim().toLowerCase() || undefined) });
  const incidents = useQuery({ queryKey: ["incidents", "recent"], queryFn: () => incidentsApi.list({ limit: 100 }) });

  // incidents opened within 60 minutes after a deployment of the same service hint at a regression
  const related = (svc: string, at: string) =>
    incidents.data?.items.filter((i) => i.service === svc && new Date(i.created_at).getTime() - new Date(at).getTime() >= 0
      && new Date(i.created_at).getTime() - new Date(at).getTime() <= 3_600_000) ?? [];

  return (
    <>
      <PageHeader title="Deployments" subtitle="Release history recorded by CI/CD; used by RCA to link incidents to releases" />
      <TextField size="small" label="Filter by service" value={service} onChange={(e) => setService(e.target.value)} sx={{ mb: 2 }} />
      <ErrorAlert error={deployments.error} />
      {deployments.isLoading ? <Loading /> : (deployments.data?.length ?? 0) === 0 ? <EmptyState message="No deployments recorded. CI/CD posts them to POST /api/v1/deployments." /> : (
        <Paper variant="outlined" sx={{ overflowX: "auto" }}>
          <Table size="small" aria-label="Deployments">
            <TableHead><TableRow><TableCell>When</TableCell><TableCell>Service</TableCell><TableCell>Version</TableCell><TableCell>Env</TableCell><TableCell>Status</TableCell><TableCell>By</TableCell><TableCell>Incidents within 60 min</TableCell></TableRow></TableHead>
            <TableBody>
              {deployments.data?.map((d) => (
                <TableRow key={d.id} hover>
                  <TableCell title={formatDateTime(d.deployed_at)}>{timeAgo(d.deployed_at)}</TableCell>
                  <TableCell>{d.service}</TableCell>
                  <TableCell>{d.version}{d.commit_sha ? ` (${d.commit_sha.slice(0, 7)})` : ""}</TableCell>
                  <TableCell>{d.environment}</TableCell>
                  <TableCell><StatusChip status={d.status === "succeeded" ? "healthy" : d.status === "failed" ? "critical" : "unknown"} /></TableCell>
                  <TableCell>{d.deployed_by || d.source}</TableCell>
                  <TableCell>{related(d.service, d.deployed_at).map((i) => <Link key={i.id} component={RouterLink} to={`/incidents/${i.id}`} sx={{ mr: 1 }}>INC-{i.id}</Link>)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}
    </>
  );
}

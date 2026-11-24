import { Box, Grid2 as Grid, List, ListItem, ListItemText, Paper, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Legend, Pie, PieChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { incidentsApi, reportingApi } from "../api/endpoints";
import { EmptyState, ErrorAlert, Loading, PageHeader, StatCard } from "../components/Common";
import { IncidentTable } from "../incidents/IncidentTable";
import { useAppSelector } from "../store";
import { formatDuration, humanize, percent, timeAgo } from "../utils/format";

export const SEVERITY_COLORS: Record<string, string> = { P1: "#d32f2f", P2: "#ed6c02", P3: "#0288d1", P4: "#9e9e9e" };

export function DashboardPage() {
  const stats = useQuery({ queryKey: ["stats", 14], queryFn: () => incidentsApi.stats(14) });
  const kpis = useQuery({ queryKey: ["kpis", 30], queryFn: () => reportingApi.kpis(30) });
  const active = useQuery({ queryKey: ["incidents", "active"], queryFn: () => incidentsApi.list({ active: true, limit: 8 }) });
  const feed = useAppSelector((s) => s.ui.feed);

  const severityData = Object.entries(stats.data?.by_severity ?? {}).map(([name, value]) => ({ name, value }));
  const p1p2 = (stats.data?.by_severity.P1 ?? 0) + (stats.data?.by_severity.P2 ?? 0);

  return (
    <>
      <PageHeader title="Operations dashboard" subtitle="Live view of incidents, remediation and platform KPIs" />
      <ErrorAlert error={stats.error ?? kpis.error ?? active.error} />
      <Grid container spacing={2}>
        <Grid size={{ xs: 6, md: 2 }}><StatCard label="Active incidents" value={active.data?.total ?? "-"} /></Grid>
        <Grid size={{ xs: 6, md: 2 }}><StatCard label="P1 / P2 (14d)" value={p1p2} color={p1p2 ? "#d32f2f" : undefined} /></Grid>
        <Grid size={{ xs: 6, md: 2 }}><StatCard label="MTTR (30d)" value={formatDuration(kpis.data?.mttr_seconds)} /></Grid>
        <Grid size={{ xs: 6, md: 2 }}><StatCard label="RCA accuracy" value={percent(kpis.data?.rca_accuracy)} hint={`${kpis.data?.rca_feedback_count ?? 0} reviews`} /></Grid>
        <Grid size={{ xs: 6, md: 2 }}><StatCard label="Remediation success" value={percent(kpis.data?.remediation_success_rate)} /></Grid>
        <Grid size={{ xs: 6, md: 2 }}><StatCard label="Alerts (14d)" value={stats.data?.alerts_received ?? "-"} /></Grid>

        <Grid size={{ xs: 12, md: 8 }}>
          <Paper variant="outlined" sx={{ p: 2, height: 320 }}>
            <Typography variant="subtitle1" gutterBottom>Incident trend by severity</Typography>
            {stats.isLoading ? <Loading /> : (stats.data?.trend.length ?? 0) === 0 ? <EmptyState message="No incidents in this window." /> : (
              <ResponsiveContainer width="100%" height="90%">
                <BarChart data={stats.data?.trend}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" /><YAxis allowDecimals={false} /><Tooltip /><Legend />
                  {(["P1", "P2", "P3", "P4"] as const).map((s) => <Bar key={s} dataKey={s} stackId="a" fill={SEVERITY_COLORS[s]} />)}
                </BarChart>
              </ResponsiveContainer>
            )}
          </Paper>
        </Grid>
        <Grid size={{ xs: 12, md: 4 }}>
          <Paper variant="outlined" sx={{ p: 2, height: 320 }}>
            <Typography variant="subtitle1" gutterBottom>Severity mix</Typography>
            {severityData.length === 0 ? <EmptyState message="No data" /> : (
              <ResponsiveContainer width="100%" height="90%">
                <PieChart>
                  <Pie data={severityData} dataKey="value" nameKey="name" innerRadius={55} outerRadius={90} label>
                    {severityData.map((d) => <Cell key={d.name} fill={SEVERITY_COLORS[d.name] ?? "#9e9e9e"} />)}
                  </Pie>
                  <Tooltip /><Legend />
                </PieChart>
              </ResponsiveContainer>
            )}
          </Paper>
        </Grid>

        <Grid size={{ xs: 12, md: 8 }}>
          <Typography variant="subtitle1" gutterBottom>Active incident panel</Typography>
          {active.isLoading ? <Loading /> : <IncidentTable incidents={active.data?.items ?? []} />}
        </Grid>
        <Grid size={{ xs: 12, md: 4 }}>
          <Typography variant="subtitle1" gutterBottom>Live activity</Typography>
          <Paper variant="outlined" sx={{ maxHeight: 360, overflow: "auto" }}>
            {feed.length === 0 ? <Box sx={{ p: 2, color: "text.secondary" }}>Waiting for events...</Box> : (
              <List dense>
                {feed.map((e) => (
                  <ListItem key={e.id} divider>
                    <ListItemText primary={humanize(e.name.replace(".", " "))}
                      secondary={`${String(e.payload.title ?? e.payload.name ?? e.payload.action ?? e.payload.service ?? e.payload.id ?? "")} · ${timeAgo(e.at)}`} />
                  </ListItem>
                ))}
              </List>
            )}
          </Paper>
        </Grid>
      </Grid>
    </>
  );
}

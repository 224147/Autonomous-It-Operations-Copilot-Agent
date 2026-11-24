import { Grid2 as Grid, MenuItem, Paper, TextField, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { incidentsApi, reportingApi } from "../api/endpoints";
import { EmptyState, ErrorAlert, Loading, PageHeader, StatCard } from "../components/Common";
import { SEVERITY_COLORS } from "../dashboard/DashboardPage";
import { formatDuration, humanize, percent } from "../utils/format";

function toBars(record: Record<string, number> | undefined, label = (k: string) => k) {
  return Object.entries(record ?? {}).map(([k, v]) => ({ name: label(k), value: v })).sort((a, b) => b.value - a.value);
}

function TargetCard({ label, value, target, note }: { label: string; value: number | null; target: number; note?: string }) {
  const ok = value !== null && value >= target;
  return <StatCard label={label} value={percent(value)} hint={`target ≥ ${percent(target)}${note ? ` · ${note}` : ""}`} color={value === null ? undefined : ok ? "#2e7d32" : "#ed6c02"} />;
}

export function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const stats = useQuery({ queryKey: ["stats", days], queryFn: () => incidentsApi.stats(days) });
  const kpis = useQuery({ queryKey: ["kpis", days], queryFn: () => reportingApi.kpis(days) });
  const s = stats.data;
  const t = kpis.data?.targets ?? {};

  return (
    <>
      <PageHeader title="Incident trend analytics" subtitle="Trends, MTTR and success-criteria KPIs measured from live data"
        actions={<TextField select size="small" label="Window" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 30, 90, 365].map((d) => <MenuItem key={d} value={d}>{d} days</MenuItem>)}
        </TextField>} />
      <ErrorAlert error={stats.error ?? kpis.error} />
      {stats.isLoading ? <Loading rows={6} /> : (
        <Grid container spacing={2}>
          <Grid size={{ xs: 6, md: 3 }}><StatCard label="Incidents" value={s?.total_incidents ?? 0} hint={`${s?.resolved_incidents ?? 0} resolved`} /></Grid>
          <Grid size={{ xs: 6, md: 3 }}><StatCard label="Mean time to resolve" value={formatDuration(s?.mttr_seconds)} /></Grid>
          <Grid size={{ xs: 6, md: 3 }}><TargetCard label="RCA accuracy" value={kpis.data?.rca_accuracy ?? null} target={t.rca_accuracy ?? 0.85} note={`${kpis.data?.rca_feedback_count ?? 0} reviews`} /></Grid>
          <Grid size={{ xs: 6, md: 3 }}><TargetCard label="Automated remediation coverage" value={kpis.data?.automated_remediation_coverage ?? null} target={t.automated_remediation_coverage ?? 0.7} /></Grid>

          <Grid size={12}>
            <Paper variant="outlined" sx={{ p: 2, height: 320 }}>
              <Typography variant="subtitle1">Daily incidents</Typography>
              {(s?.trend.length ?? 0) === 0 ? <EmptyState message="No incidents in this window." /> : (
                <ResponsiveContainer width="100%" height="90%">
                  <LineChart data={s?.trend}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" /><YAxis allowDecimals={false} /><Tooltip /><Legend />
                    <Line type="monotone" dataKey="total" stroke="#1a4fa0" strokeWidth={2} />
                    {(["P1", "P2"] as const).map((k) => <Line key={k} type="monotone" dataKey={k} stroke={SEVERITY_COLORS[k]} />)}
                  </LineChart>
                </ResponsiveContainer>
              )}
            </Paper>
          </Grid>
          {([["By category", toBars(s?.by_category, humanize)], ["By service", toBars(s?.by_service)], ["By status", toBars(s?.by_status, humanize)]] as const).map(([title, data]) => (
            <Grid key={title} size={{ xs: 12, md: 4 }}>
              <Paper variant="outlined" sx={{ p: 2, height: 300 }}>
                <Typography variant="subtitle1">{title}</Typography>
                {data.length === 0 ? <EmptyState message="No data" /> : (
                  <ResponsiveContainer width="100%" height="90%">
                    <BarChart data={data} layout="vertical" margin={{ left: 40 }}><CartesianGrid strokeDasharray="3 3" /><XAxis type="number" allowDecimals={false} /><YAxis type="category" dataKey="name" width={130} /><Tooltip />
                      <Bar dataKey="value" fill="#1a4fa0" /></BarChart>
                  </ResponsiveContainer>
                )}
              </Paper>
            </Grid>
          ))}
          <Grid size={12}>
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Typography variant="subtitle2" gutterBottom>Success criteria that cannot be measured by the platform alone</Typography>
              {Object.entries(kpis.data?.notes ?? {}).map(([k, v]) => <Typography key={k} variant="body2" color="text.secondary">• <strong>{humanize(k)}:</strong> {v}</Typography>)}
            </Paper>
          </Grid>
        </Grid>
      )}
    </>
  );
}

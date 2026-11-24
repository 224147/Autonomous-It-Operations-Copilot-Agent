import { Box, Card, CardContent, Chip, Grid2 as Grid, Link, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";

import { monitoringApi } from "../api/endpoints";
import { SeverityChip, StatusChip } from "../components/Chips";
import { EmptyState, ErrorAlert, Loading, PageHeader } from "../components/Common";
import type { ServiceHealth } from "../types";

const BORDER: Record<ServiceHealth["state"], string> = { healthy: "#2e7d32", degraded: "#ed6c02", critical: "#d32f2f", unknown: "#9e9e9e" };
const METRIC_LABELS: Record<string, string> = {
  cpu_usage_percent: "CPU %", memory_usage_percent: "Mem %", error_rate_percent: "Err %", latency_p99_ms: "p99 ms", restart_count: "Restarts",
};

export function ServiceHealthPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: monitoringApi.serviceHealth, refetchInterval: 15_000 });
  return (
    <>
      <PageHeader title="Service health" subtitle="State derived from the freshest metrics, logs, traces and open incidents" />
      <ErrorAlert error={health.error} />
      {health.isLoading ? <Loading /> : (health.data?.length ?? 0) === 0 ? <EmptyState message="The service inventory is empty." /> : (
        <Grid container spacing={2}>
          {health.data?.map((svc) => (
            <Grid key={svc.name} size={{ xs: 12, sm: 6, lg: 4 }}>
              <Card variant="outlined" sx={{ borderLeft: `6px solid ${BORDER[svc.state]}`, height: "100%" }}>
                <CardContent>
                  <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography variant="h6">{svc.name}</Typography>
                    <StatusChip status={svc.state} />
                  </Stack>
                  <Typography variant="caption" color="text.secondary">tier {svc.tier} · {svc.kind}{svc.team ? ` · ${svc.team}` : ""}</Typography>
                  <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ my: 1 }}>
                    {Object.entries(svc.metrics).map(([k, v]) => <Chip key={k} size="small" label={`${METRIC_LABELS[k] ?? k}: ${v}`} />)}
                    {Object.keys(svc.metrics).length === 0 && <Typography variant="caption" color="text.secondary">No recent metrics</Typography>}
                  </Stack>
                  {svc.anomalies.map((a) => <Typography key={a.name} variant="body2" color="error">• {a.description}</Typography>)}
                  {svc.depends_on.length > 0 && <Typography variant="caption" color="text.secondary" component="div">depends on: {svc.depends_on.join(", ")}</Typography>}
                  {svc.active_incidents.map((i) => (
                    <Box key={i.id} sx={{ mt: 1, display: "flex", gap: 1, alignItems: "center" }}>
                      <SeverityChip severity={i.severity} />
                      <Link component={RouterLink} to={`/incidents/${i.id}`} variant="body2">INC-{i.id}</Link>
                    </Box>
                  ))}
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}
    </>
  );
}

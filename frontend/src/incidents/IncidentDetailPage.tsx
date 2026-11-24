import ThumbDown from "@mui/icons-material/ThumbDownOutlined";
import ThumbUp from "@mui/icons-material/ThumbUpOutlined";
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Grid2 as Grid, Paper, Stack, Tab, Tabs, TextField, Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import { incidentsApi, rcaApi } from "../api/endpoints";
import { RiskChip, SeverityChip, StatusChip } from "../components/Chips";
import { ConfidenceGauge } from "../components/ConfidenceGauge";
import { EmptyState, ErrorAlert, Loading, PageHeader } from "../components/Common";
import { useRole } from "../hooks/useRole";
import { ApprovalCard } from "../remediation/ApprovalCard";
import type { IncidentDetail, TimelineItem } from "../types";
import { formatDateTime, humanize } from "../utils/format";
import { RcaTimeline } from "./RcaTimeline";

const TERMINAL = ["resolved", "closed", "false_positive"];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle1" gutterBottom>{title}</Typography>
      {children}
    </Paper>
  );
}

function RcaPanel({ incident }: { incident: IncidentDetail }) {
  const { has } = useRole();
  const queryClient = useQueryClient();
  const rca = incident.rca;
  const feedback = useMutation({
    mutationFn: (result: "correct" | "incorrect") => rcaApi.feedback(rca?.id ?? 0, result),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["incident", incident.id] }),
  });
  if (!rca) return <EmptyState message={incident.workflow_status === "running" ? "Analysis in progress..." : "No root cause analysis yet."} />;
  return (
    <Section title="Root cause analysis">
      <Stack direction={{ xs: "column", sm: "row" }} spacing={3} alignItems={{ sm: "center" }}>
        <ConfidenceGauge value={rca.confidence} />
        <Box>
          <Typography variant="h6">{rca.root_cause}</Typography>
          <Chip size="small" label={humanize(rca.category)} sx={{ mt: 0.5 }} />
          <Typography variant="body2" sx={{ mt: 1 }}>{rca.explanation}</Typography>
        </Box>
      </Stack>
      {rca.contributing_factors.length > 0 && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2">Contributing factors</Typography>
          <ul>{rca.contributing_factors.map((f) => <li key={f}><Typography variant="body2">{f}</Typography></li>)}</ul>
        </Box>
      )}
      {rca.evidence.length > 0 && (
        <Box>
          <Typography variant="subtitle2">Evidence</Typography>
          <ul>{rca.evidence.slice(0, 12).map((e, i) => <li key={i}><Typography variant="body2"><strong>{e.type}</strong> {e.service ? `(${e.service}) ` : ""}{e.detail}</Typography></li>)}</ul>
        </Box>
      )}
      {has("operator") && (
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2">Was this analysis correct?</Typography>
          <Button size="small" startIcon={<ThumbUp />} variant={rca.feedback === "correct" ? "contained" : "outlined"} color="success"
            onClick={() => feedback.mutate("correct")} disabled={feedback.isPending}>Correct</Button>
          <Button size="small" startIcon={<ThumbDown />} variant={rca.feedback === "incorrect" ? "contained" : "outlined"} color="error"
            onClick={() => feedback.mutate("incorrect")} disabled={feedback.isPending}>Incorrect</Button>
        </Stack>
      )}
    </Section>
  );
}

export function IncidentDetailPage() {
  const { id } = useParams();
  const incidentId = Number(id);
  const { has } = useRole();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState(0);
  const [resolveOpen, setResolveOpen] = useState(false);
  const [notes, setNotes] = useState("");

  const query = useQuery({ queryKey: ["incident", incidentId], queryFn: () => incidentsApi.get(incidentId), enabled: Number.isFinite(incidentId),
    refetchInterval: (q) => (q.state.data?.workflow_status === "running" ? 4000 : false) });
  const report = useQuery({ queryKey: ["incident", incidentId, "report"], queryFn: () => incidentsApi.report(incidentId), enabled: tab === 3 });
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ["incident", incidentId] }); void queryClient.invalidateQueries({ queryKey: ["incidents"] }); };
  const analyze = useMutation({ mutationFn: () => incidentsApi.analyze(incidentId), onSuccess: refresh });
  const resolve = useMutation({ mutationFn: () => incidentsApi.resolve(incidentId, notes), onSuccess: () => { setResolveOpen(false); refresh(); } });

  if (query.isLoading) return <Loading rows={8} />;
  if (query.error || !query.data) return <ErrorAlert error={query.error ?? new Error("Incident not found")} />;
  const inc = query.data;
  const timeline: TimelineItem[] = inc.rca?.timeline?.length ? inc.rca.timeline : inc.correlation.timeline ?? [];
  const pendingApprovals = inc.approvals.filter((a) => a.status === "pending");
  const paths = inc.correlation.propagation_paths ?? [];

  return (
    <>
      <PageHeader title={`INC-${inc.id}: ${inc.title}`} subtitle={inc.summary}
        actions={<>
          {has("operator") && <Button variant="outlined" onClick={() => analyze.mutate()} disabled={analyze.isPending}>Re-run analysis</Button>}
          {has("operator") && !TERMINAL.includes(inc.status) && <Button variant="contained" color="success" onClick={() => setResolveOpen(true)}>Resolve</Button>}
        </>} />
      <Stack direction="row" spacing={1} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
        <SeverityChip severity={inc.severity} /><StatusChip status={inc.status} />
        <Chip size="small" label={`service: ${inc.service}`} />
        {inc.affected_services.filter((s) => s !== inc.service).map((s) => <Chip key={s} size="small" variant="outlined" label={s} />)}
        <Chip size="small" variant="outlined" label={`workflow: ${inc.workflow_status}`} />
        <Chip size="small" variant="outlined" label={`opened ${formatDateTime(inc.created_at)}`} />
      </Stack>
      <ErrorAlert error={analyze.error} />
      {pendingApprovals.length > 0 && <Alert severity="warning" sx={{ mb: 2 }}>This incident has a remediation plan awaiting approval.</Alert>}

      <Tabs value={tab} onChange={(_, v: number) => setTab(v)} sx={{ mb: 2 }} variant="scrollable">
        <Tab label="Analysis" /><Tab label="Remediation" /><Tab label="Workflow" /><Tab label="Report" />
      </Tabs>

      {tab === 0 && (
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, lg: 7 }}>
            <RcaPanel incident={inc} />
            {paths.length > 0 && (
              <Section title="Failure propagation">
                {paths.map((p, i) => <Typography key={i} variant="body2">{p.join(" → ")}</Typography>)}
              </Section>
            )}
            {(inc.correlation.recent_deployments ?? []).length > 0 && (
              <Section title="Recent deployments">
                {inc.correlation.recent_deployments?.map((d, i) => (
                  <Typography key={i} variant="body2">{d.service} {d.version} at {formatDateTime(d.deployed_at)}{d.minutes_before_anomaly !== undefined ? ` (${d.minutes_before_anomaly} min before onset)` : ""}</Typography>
                ))}
              </Section>
            )}
          </Grid>
          <Grid size={{ xs: 12, lg: 5 }}>
            <Section title="RCA timeline"><RcaTimeline items={timeline} /></Section>
          </Grid>
        </Grid>
      )}

      {tab === 1 && (
        <>
          <Section title="Recommended actions">
            {inc.recommendations.length === 0 ? <Typography color="text.secondary">No recommendations yet.</Typography> : inc.recommendations.map((a) => (
              <Box key={a.id} sx={{ py: 0.5 }}>
                <Typography variant="body2" component="div">{a.rank}. <strong>{humanize(a.action)}</strong> on <code>{a.target}</code> <RiskChip risk={a.risk} /> score {a.score}</Typography>
                <Typography variant="caption" color="text.secondary">{a.rationale}</Typography>
              </Box>
            ))}
          </Section>
          <Typography variant="subtitle1" gutterBottom>Approvals</Typography>
          {inc.approvals.length === 0 ? <EmptyState message="No approval requests." /> : inc.approvals.map((a) => <ApprovalCard key={a.id} approval={a} />)}
          <Section title="Executions">
            {inc.executions.length === 0 ? <Typography color="text.secondary">Nothing executed yet.</Typography> : inc.executions.map((e) => (
              <Typography key={e.id} variant="body2" component="div">{formatDateTime(e.started_at)} · {humanize(e.action)} on {e.target} · <StatusChip status={e.status} /> ({e.mode}){e.error ? ` - ${e.error}` : ""}</Typography>
            ))}
            {inc.verification.status && <Alert sx={{ mt: 1 }} severity={inc.verification.status === "healthy" ? "success" : inc.verification.status === "unhealthy" ? "error" : "info"}>
              Verification: {inc.verification.status} - {inc.verification.details}</Alert>}
          </Section>
          <Section title="Tickets">
            {inc.tickets.length === 0 ? <Typography color="text.secondary">No tickets.</Typography> : inc.tickets.map((t) => (
              <Typography key={t.id} variant="body2">#{t.id} {t.title} {t.external_ref ? `(${t.external_ref})` : ""}</Typography>
            ))}
          </Section>
        </>
      )}

      {tab === 2 && <Section title="Agent workflow trace">
        {inc.events.length === 0 ? <Typography color="text.secondary">No workflow events yet.</Typography> : <RcaTimeline items={inc.events.map((e) => ({ timestamp: e.timestamp, source: "workflow", stage: e.stage, description: e.message }))} />}
      </Section>}

      {tab === 3 && <Section title="Incident report (Markdown)">
        {report.isLoading ? <Loading /> : <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: 13, m: 0 }}>{report.data?.markdown || "The report is generated when the workflow completes."}</Box>}
      </Section>}

      <Dialog open={resolveOpen} onClose={() => setResolveOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Resolve INC-{inc.id}</DialogTitle>
        <DialogContent>
          {resolve.error && <Alert severity="error" sx={{ mb: 2 }}>{errorMessage(resolve.error)}</Alert>}
          <TextField sx={{ mt: 1 }} fullWidth multiline minRows={3} label="Resolution notes (stored in the knowledge base)" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setResolveOpen(false)}>Cancel</Button>
          <Button variant="contained" color="success" disabled={resolve.isPending} onClick={() => resolve.mutate()}>Resolve</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

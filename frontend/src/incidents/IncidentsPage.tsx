import { Button, FormControlLabel, MenuItem, Pagination, Stack, Switch, TextField } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { incidentsApi } from "../api/endpoints";
import { ErrorAlert, Loading, PageHeader } from "../components/Common";
import { useRole } from "../hooks/useRole";
import { IncidentTable } from "./IncidentTable";
import { NewIncidentDialog } from "./NewIncidentDialog";

const PAGE = 25;
const STATUSES = ["", "open", "investigating", "awaiting_approval", "monitoring", "escalated", "resolved", "false_positive", "closed"];

export function IncidentsPage() {
  const { has } = useRole();
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [service, setService] = useState("");
  const [active, setActive] = useState(false);
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);

  const query = useQuery({
    queryKey: ["incidents", { status, severity, service, active, page }],
    queryFn: () => incidentsApi.list({ status: status || undefined, severity: severity || undefined, service: service || undefined, active, limit: PAGE, offset: (page - 1) * PAGE }),
  });

  return (
    <>
      <PageHeader title="Incidents" subtitle="Detected and reported incidents"
        actions={has("operator") ? <Button variant="contained" onClick={() => setCreating(true)}>Report incident</Button> : undefined} />
      <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ mb: 2 }} alignItems={{ md: "center" }}>
        <TextField select size="small" label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} sx={{ minWidth: 180 }}>
          {STATUSES.map((s) => <MenuItem key={s} value={s}>{s || "Any"}</MenuItem>)}
        </TextField>
        <TextField select size="small" label="Severity" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }} sx={{ minWidth: 140 }}>
          {["", "P1", "P2", "P3", "P4"].map((s) => <MenuItem key={s} value={s}>{s || "Any"}</MenuItem>)}
        </TextField>
        <TextField size="small" label="Service" value={service} onChange={(e) => { setService(e.target.value); setPage(1); }} />
        <FormControlLabel control={<Switch checked={active} onChange={(e) => { setActive(e.target.checked); setPage(1); }} />} label="Active only" />
      </Stack>
      <ErrorAlert error={query.error} />
      {query.isLoading ? <Loading /> : <IncidentTable incidents={query.data?.items ?? []} />}
      {(query.data?.total ?? 0) > PAGE && (
        <Pagination sx={{ mt: 2 }} count={Math.ceil((query.data?.total ?? 0) / PAGE)} page={page} onChange={(_, p) => setPage(p)} />
      )}
      <NewIncidentDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

import { Alert, Button, Chip, Pagination, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField } from "@mui/material";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { adminApi } from "../api/endpoints";
import { EmptyState, ErrorAlert, Loading, PageHeader } from "../components/Common";
import { formatDateTime } from "../utils/format";

const PAGE = 50;

export function AuditPage() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const logs = useQuery({
    queryKey: ["audit", { actor, action, page }],
    queryFn: () => adminApi.audit({ limit: PAGE, offset: (page - 1) * PAGE, actor: actor || undefined, action: action || undefined }),
  });
  const verify = useMutation({ mutationFn: adminApi.verifyAudit });

  return (
    <>
      <PageHeader title="Audit logs" subtitle="Append-only, hash-chained record of every sensitive operation"
        actions={<Button variant="outlined" onClick={() => verify.mutate()} disabled={verify.isPending}>Verify integrity</Button>} />
      {verify.data && (
        <Alert severity={verify.data.valid ? "success" : "error"} sx={{ mb: 2 }}>
          {verify.data.valid ? `Hash chain intact (${verify.data.checked} entries verified).` : `Chain broken at entry #${verify.data.broken_at}.`}
        </Alert>
      )}
      <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ mb: 2 }}>
        <TextField size="small" label="Actor (exact)" value={actor} onChange={(e) => { setActor(e.target.value); setPage(1); }} />
        <TextField size="small" label="Action prefix (e.g. approval.)" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} />
      </Stack>
      <ErrorAlert error={logs.error ?? verify.error} />
      {logs.isLoading ? <Loading /> : (logs.data?.items.length ?? 0) === 0 ? <EmptyState message="No audit entries." /> : (
        <Paper variant="outlined" sx={{ overflowX: "auto" }}>
          <Table size="small" aria-label="Audit log">
            <TableHead><TableRow><TableCell>#</TableCell><TableCell>Time</TableCell><TableCell>Actor</TableCell><TableCell>Action</TableCell><TableCell>Resource</TableCell><TableCell>Outcome</TableCell><TableCell>IP</TableCell><TableCell>Details</TableCell></TableRow></TableHead>
            <TableBody>
              {logs.data?.items.map((e) => (
                <TableRow key={e.id} hover>
                  <TableCell>{e.id}</TableCell><TableCell>{formatDateTime(e.timestamp)}</TableCell>
                  <TableCell>{e.actor}{e.actor_role ? ` (${e.actor_role})` : ""}</TableCell><TableCell>{e.action}</TableCell>
                  <TableCell>{e.resource_type} {e.resource_id}</TableCell>
                  <TableCell><Chip size="small" color={e.outcome === "success" ? "success" : "error"} label={e.outcome} variant="outlined" /></TableCell>
                  <TableCell>{e.ip_address}</TableCell>
                  <TableCell sx={{ maxWidth: 320, fontFamily: "monospace", fontSize: 12 }}>{JSON.stringify(e.details).slice(0, 160)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}
      {(logs.data?.total ?? 0) > PAGE && <Pagination sx={{ mt: 2 }} count={Math.ceil((logs.data?.total ?? 0) / PAGE)} page={page} onChange={(_, p) => setPage(p)} />}
    </>
  );
}

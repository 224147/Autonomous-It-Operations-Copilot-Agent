import {
  Alert, Box, Button, Card, CardContent, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { remediationApi } from "../api/endpoints";
import { RiskChip, StatusChip } from "../components/Chips";
import { useRole } from "../hooks/useRole";
import type { Approval } from "../types";
import { formatDateTime, humanize } from "../utils/format";

/** One approval request: plan actions with checkboxes (partial approval), approve / reject with a comment. */
export function ApprovalCard({ approval }: { approval: Approval }) {
  const { has } = useRole();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>(() => approval.plan.map((a) => a.id));
  const [dialog, setDialog] = useState<"approve" | "reject" | null>(null);
  const [comment, setComment] = useState("");
  const pending = approval.status === "pending";

  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") =>
      remediationApi.decide(approval.id, decision, comment, decision === "approve" && selected.length !== approval.plan.length ? selected : []),
    onSuccess: () => {
      setDialog(null);
      setComment("");
      for (const key of ["approvals", "incident", "incidents"]) void queryClient.invalidateQueries({ queryKey: [key] });
    },
  });

  const toggle = (id: string) => setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
          <Typography variant="subtitle1">Approval #{approval.id}</Typography>
          <StatusChip status={approval.status} />
          <RiskChip risk={approval.risk} />
          {approval.auto_approved && <StatusChip status="approved" />}
          <Typography variant="caption" color="text.secondary">requested {formatDateTime(approval.requested_at)}
            {approval.expires_at && pending ? ` · expires ${formatDateTime(approval.expires_at)}` : ""}</Typography>
        </Stack>
        {approval.plan.map((a) => (
          <Box key={a.id} sx={{ py: 0.5, display: "flex", gap: 1, alignItems: "flex-start" }}>
            {pending && has("approver") && (
              <Checkbox size="small" checked={selected.includes(a.id)} onChange={() => toggle(a.id)} inputProps={{ "aria-label": `Select ${a.action}` }} />
            )}
            <Box>
              <Typography variant="body2" component="div"><strong>{humanize(a.action)}</strong> on <code>{a.target}</code> <RiskChip risk={a.risk} /></Typography>
              {a.rationale && <Typography variant="caption" color="text.secondary">{a.rationale}{a.score !== undefined ? ` (score ${a.score})` : ""}</Typography>}
            </Box>
          </Box>
        ))}
        {!pending && approval.decided_by && (
          <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>
            {approval.status} by {approval.decided_by} · {formatDateTime(approval.decided_at)}{approval.comment ? ` · "${approval.comment}"` : ""}
          </Typography>
        )}
        {pending && !has("approver") && <Alert severity="info" sx={{ mt: 1 }}>An approver or administrator must decide on this plan.</Alert>}
        {pending && has("approver") && (
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button variant="contained" color="success" disabled={selected.length === 0} onClick={() => setDialog("approve")}>
              {selected.length === approval.plan.length ? "Approve all" : `Approve ${selected.length} selected`}
            </Button>
            <Button variant="outlined" color="error" onClick={() => setDialog("reject")}>Reject</Button>
          </Stack>
        )}
      </CardContent>
      <Dialog open={dialog !== null} onClose={() => setDialog(null)} fullWidth maxWidth="sm">
        <DialogTitle>{dialog === "approve" ? "Approve remediation" : "Reject remediation"}</DialogTitle>
        <DialogContent>
          {decide.error && <Alert severity="error" sx={{ mb: 2 }}>{errorMessage(decide.error)}</Alert>}
          <Typography variant="body2" sx={{ mb: 2 }}>
            {dialog === "approve" ? "The selected actions will be executed by the remediation engine (honouring the configured dry-run/live mode)." : "No recovery action will be executed; the incident is escalated."}
          </Typography>
          <TextField fullWidth multiline minRows={2} label="Comment (recorded in the audit trail)" value={comment} onChange={(e) => setComment(e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(null)}>Cancel</Button>
          <Button variant="contained" color={dialog === "approve" ? "success" : "error"} disabled={decide.isPending}
            onClick={() => dialog && decide.mutate(dialog)}>Confirm {dialog}</Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}

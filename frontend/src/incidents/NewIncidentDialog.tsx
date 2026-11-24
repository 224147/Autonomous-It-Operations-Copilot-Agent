import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { errorMessage } from "../api/client";
import { incidentsApi } from "../api/endpoints";

export function NewIncidentDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [service, setService] = useState("");
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState("P3");
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () => incidentsApi.create({ service: service.trim().toLowerCase(), title, severity, description }),
    onSuccess: (incident) => {
      void queryClient.invalidateQueries({ queryKey: ["incidents"] });
      onClose();
      navigate(`/incidents/${incident.id}`);
    },
  });

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Report an incident</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {create.error && <Alert severity="error">{errorMessage(create.error)}</Alert>}
          <TextField label="Service (must exist in the inventory)" value={service} onChange={(e) => setService(e.target.value)} required />
          <TextField label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
          <TextField select label="Initial severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {["P1", "P2", "P3", "P4"].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </TextField>
          <TextField label="Description" multiline minRows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!service.trim() || create.isPending} onClick={() => create.mutate()}>Create & analyze</Button>
      </DialogActions>
    </Dialog>
  );
}

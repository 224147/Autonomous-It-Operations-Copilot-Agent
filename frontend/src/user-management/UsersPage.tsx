import { Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Paper, Select, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { adminApi } from "../api/endpoints";
import { ErrorAlert, Loading, PageHeader } from "../components/Common";
import { useAppSelector } from "../store";
import type { Role } from "../types";
import { formatDateTime } from "../utils/format";

const ROLES: Role[] = ["viewer", "operator", "approver", "admin"];

function CreateUserDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "viewer" });
  const create = useMutation({
    mutationFn: () => adminApi.createUser(form),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["users"] }); setForm({ email: "", full_name: "", password: "", role: "viewer" }); onClose(); },
  });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Create user</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {create.error && <Alert severity="error">{errorMessage(create.error)}</Alert>}
          <TextField label="Email" type="email" value={form.email} onChange={set("email")} />
          <TextField label="Full name" value={form.full_name} onChange={set("full_name")} />
          <TextField label="Initial password" type="password" autoComplete="new-password" value={form.password} onChange={set("password")} helperText="At least 10 characters, letters and digits" />
          <TextField select label="Role" value={form.role} onChange={set("role")}>{ROLES.map((r) => <MenuItem key={r} value={r}>{r}</MenuItem>)}</TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.email || form.password.length < 10 || create.isPending} onClick={() => create.mutate()}>Create</Button>
      </DialogActions>
    </Dialog>
  );
}

export function UsersPage() {
  const queryClient = useQueryClient();
  const me = useAppSelector((s) => s.auth.user);
  const [creating, setCreating] = useState(false);
  const users = useQuery({ queryKey: ["users"], queryFn: adminApi.users });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<{ role: string; is_active: boolean }> }) => adminApi.updateUser(id, body),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["users"] }),
  });

  return (
    <>
      <PageHeader title="User management" subtitle="Roles: viewer < operator < approver < admin"
        actions={<Button variant="contained" onClick={() => setCreating(true)}>Create user</Button>} />
      <ErrorAlert error={users.error ?? update.error} />
      {users.isLoading ? <Loading /> : (
        <Paper variant="outlined" sx={{ overflowX: "auto" }}>
          <Table size="small" aria-label="Users">
            <TableHead><TableRow><TableCell>Email</TableCell><TableCell>Name</TableCell><TableCell>Provider</TableCell><TableCell>Role</TableCell><TableCell>Active</TableCell><TableCell>Last login</TableCell></TableRow></TableHead>
            <TableBody>
              {users.data?.map((u) => {
                const self = u.id === me?.id;
                return (
                  <TableRow key={u.id} hover>
                    <TableCell>{u.email} {self && <Chip size="small" label="you" />}</TableCell>
                    <TableCell>{u.full_name}</TableCell>
                    <TableCell>{u.auth_provider}</TableCell>
                    <TableCell>
                      <Select size="small" value={u.role} disabled={self} onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } })} inputProps={{ "aria-label": `Role of ${u.email}` }}>
                        {ROLES.map((r) => <MenuItem key={r} value={r}>{r}</MenuItem>)}
                      </Select>
                    </TableCell>
                    <TableCell><Switch checked={u.is_active} disabled={self} onChange={(e) => update.mutate({ id: u.id, body: { is_active: e.target.checked } })} inputProps={{ "aria-label": `Active ${u.email}` }} /></TableCell>
                    <TableCell>{formatDateTime(u.last_login_at)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Paper>
      )}
      <CreateUserDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

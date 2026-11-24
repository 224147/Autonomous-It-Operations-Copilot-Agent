import MicrosoftIcon from "@mui/icons-material/Microsoft";
import ShieldIcon from "@mui/icons-material/Security";
import { Alert, Box, Button, Divider, Paper, Stack, TextField, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { errorMessage } from "../api/client";
import { authApi } from "../api/endpoints";
import { useAppDispatch, useAppSelector } from "../store";
import { login, loginWithEntra } from "../store/authSlice";
import { entraIdToken } from "./entra";

export function LoginPage() {
  const dispatch = useAppDispatch();
  const location = useLocation();
  const { status, error, accessToken } = useAppSelector((s) => s.auth);
  const config = useQuery({ queryKey: ["auth-config"], queryFn: authApi.config, staleTime: 5 * 60_000 });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [ssoError, setSsoError] = useState<string | null>(null);

  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (accessToken || status === "authenticated") return <Navigate to={from} replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void dispatch(login({ email, password }));
  };

  const sso = async () => {
    setSsoError(null);
    try {
      const { entra_tenant_id, entra_client_id } = config.data ?? {};
      if (!entra_tenant_id || !entra_client_id) throw new Error("SSO is not configured");
      void dispatch(loginWithEntra(await entraIdToken(entra_tenant_id, entra_client_id)));
    } catch (err) {
      setSsoError(errorMessage(err));
    }
  };

  return (
    <Box sx={{ minHeight: "100vh", display: "grid", placeItems: "center", p: 2, bgcolor: "background.default" }}>
      <Paper elevation={3} sx={{ p: 4, width: "100%", maxWidth: 420 }}>
        <Stack spacing={2} component="form" onSubmit={submit} noValidate>
          <Stack direction="row" spacing={1} alignItems="center">
            <ShieldIcon color="primary" fontSize="large" />
            <Box>
              <Typography variant="h5" component="h1">IT Ops Copilot</Typography>
              <Typography variant="body2" color="text.secondary">Autonomous IT Operations</Typography>
            </Box>
          </Stack>
          {(error || ssoError) && <Alert severity="error">{error ?? ssoError}</Alert>}
          <TextField label="Email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          <TextField label="Password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          <Button type="submit" variant="contained" size="large" disabled={status === "loading" || !email || !password}>Sign in</Button>
          {config.data?.entra_enabled && (
            <>
              <Divider>or</Divider>
              <Button variant="outlined" startIcon={<MicrosoftIcon />} onClick={() => void sso()} disabled={status === "loading"}>
                Sign in with Microsoft
              </Button>
            </>
          )}
        </Stack>
      </Paper>
    </Box>
  );
}

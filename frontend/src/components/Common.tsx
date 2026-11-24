import { Alert, Box, CircularProgress, Paper, Skeleton, Stack, Typography } from "@mui/material";
import type { ReactNode } from "react";

import { errorMessage } from "../api/client";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} spacing={1} sx={{ mb: 3 }}>
      <Box>
        <Typography variant="h4" component="h1">{title}</Typography>
        {subtitle && <Typography color="text.secondary">{subtitle}</Typography>}
      </Box>
      {actions && <Stack direction="row" spacing={1}>{actions}</Stack>}
    </Stack>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  return error ? <Alert severity="error" sx={{ mb: 2 }}>{errorMessage(error)}</Alert> : null;
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <Stack spacing={1} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} variant="rounded" height={36} />)}
    </Stack>
  );
}

export function Spinner() {
  return <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}><CircularProgress /></Box>;
}

export function EmptyState({ message }: { message: string }) {
  return <Paper variant="outlined" sx={{ p: 4, textAlign: "center", color: "text.secondary" }}>{message}</Paper>;
}

export function StatCard({ label, value, hint, color }: { label: string; value: ReactNode; hint?: string; color?: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
      <Typography variant="overline" color="text.secondary">{label}</Typography>
      <Typography variant="h4" sx={{ color }}>{value}</Typography>
      {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
    </Paper>
  );
}

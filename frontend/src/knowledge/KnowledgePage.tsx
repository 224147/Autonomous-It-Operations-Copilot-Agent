import { Box, Button, Chip, Dialog, DialogContent, DialogTitle, Grid2 as Grid, List, ListItemButton, ListItemText, MenuItem, Paper, Stack, TextField, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { knowledgeApi, knowledgeDocsApi } from "../api/endpoints";
import { EmptyState, ErrorAlert, Loading, PageHeader } from "../components/Common";
import { humanize } from "../utils/format";

const TYPES = ["", "runbook", "sop", "postmortem", "incident", "infrastructure"];

export function KnowledgePage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [mode, setMode] = useState<"hybrid" | "semantic" | "keyword">("hybrid");
  const [docType, setDocType] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const stats = useQuery({ queryKey: ["kb-stats"], queryFn: knowledgeDocsApi.stats });
  const docs = useQuery({ queryKey: ["kb-docs", docType], queryFn: () => knowledgeDocsApi.list(docType || undefined) });
  const results = useQuery({ queryKey: ["kb-search", submitted, mode], queryFn: () => knowledgeApi.search(submitted, mode), enabled: submitted.length > 0 });
  const doc = useQuery({ queryKey: ["kb-doc", openId], queryFn: () => knowledgeDocsApi.get(openId ?? ""), enabled: openId !== null });

  const submit = (e: FormEvent) => { e.preventDefault(); setSubmitted(query.trim()); };

  return (
    <>
      <PageHeader title="Knowledge base" subtitle={stats.data ? `${stats.data.chunks} indexed chunks · embedder ${stats.data.embedder}` : "Runbooks, SOPs, postmortems and learned incidents"} />
      <ErrorAlert error={stats.error ?? docs.error ?? results.error} />
      <Stack component="form" onSubmit={submit} direction={{ xs: "column", md: "row" }} spacing={2} sx={{ mb: 3 }}>
        <TextField fullWidth size="small" label="Search (e.g. pods OOMKilled after deploy)" value={query} onChange={(e) => setQuery(e.target.value)} />
        <TextField select size="small" label="Mode" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} sx={{ minWidth: 140 }}>
          {["hybrid", "semantic", "keyword"].map((m) => <MenuItem key={m} value={m}>{m}</MenuItem>)}
        </TextField>
        <Button type="submit" variant="contained" disabled={!query.trim()}>Search</Button>
      </Stack>

      {submitted && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle1" gutterBottom>Results for “{submitted}”</Typography>
          {results.isLoading ? <Loading /> : (results.data?.length ?? 0) === 0 ? <EmptyState message="No matching documents." /> : (
            <Paper variant="outlined">
              <List>
                {results.data?.map((r) => (
                  <ListItemButton key={r.id} divider onClick={() => setOpenId(r.id)}>
                    <ListItemText primary={String(r.metadata.title ?? r.id)} secondary={r.text.slice(0, 180)} />
                    <Chip size="small" label={String(r.metadata.doc_type ?? "")} sx={{ mr: 1 }} />
                    <Chip size="small" variant="outlined" label={`score ${r.score}`} />
                  </ListItemButton>
                ))}
              </List>
            </Paper>
          )}
        </Box>
      )}

      <Grid container spacing={2} alignItems="center" sx={{ mb: 1 }}>
        <Grid><Typography variant="subtitle1">All documents</Typography></Grid>
        <Grid>
          <TextField select size="small" label="Type" value={docType} onChange={(e) => setDocType(e.target.value)} sx={{ minWidth: 160 }}>
            {TYPES.map((t) => <MenuItem key={t} value={t}>{t ? humanize(t) : "Any"}</MenuItem>)}
          </TextField>
        </Grid>
      </Grid>
      {docs.isLoading ? <Loading /> : (docs.data?.length ?? 0) === 0 ? <EmptyState message="The knowledge base is empty." /> : (
        <Paper variant="outlined">
          <List dense>
            {docs.data?.map((d) => (
              <ListItemButton key={d.id} divider onClick={() => setOpenId(d.id)}>
                <ListItemText primary={String(d.metadata.title ?? d.id)} secondary={d.id} />
                <Chip size="small" label={String(d.metadata.doc_type ?? "")} />
              </ListItemButton>
            ))}
          </List>
        </Paper>
      )}

      <Dialog open={openId !== null} onClose={() => setOpenId(null)} fullWidth maxWidth="md">
        <DialogTitle>{String(doc.data?.metadata.title ?? openId ?? "")}</DialogTitle>
        <DialogContent>
          {doc.isLoading ? <Loading rows={5} /> : <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: 13, m: 0 }}>{doc.data?.text}</Box>}
        </DialogContent>
      </Dialog>
    </>
  );
}

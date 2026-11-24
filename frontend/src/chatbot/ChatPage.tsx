import SendIcon from "@mui/icons-material/Send";
import { Alert, Box, Chip, IconButton, Link, Paper, Stack, TextField, Typography } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link as RouterLink } from "react-router-dom";

import { errorMessage } from "../api/client";
import { chatApi } from "../api/endpoints";
import { PageHeader } from "../components/Common";
import type { ChatMessage, ChatResponse } from "../types";

const SESSION_KEY = "copilot.chat.session";
const SUGGESTIONS = ["What incidents are active?", "Why did INC-1 happen?", "Which approvals are pending?", "How do I handle a crash loop?"];

/** Answers are rendered as plain text (React escapes it): never as HTML. */
function Bubble({ message }: { message: ChatMessage }) {
  const mine = message.role === "user";
  return (
    <Box sx={{ display: "flex", justifyContent: mine ? "flex-end" : "flex-start", mb: 1.5 }}>
      <Paper elevation={0} sx={{ p: 1.5, maxWidth: "80%", bgcolor: mine ? "primary.main" : "action.hover", color: mine ? "primary.contrastText" : "text.primary" }}>
        <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{message.content}</Typography>
        {message.sources && message.sources.length > 0 && (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
            {message.sources.map((s, i) => s.type === "incident"
              ? <Chip key={i} size="small" component={RouterLink} to={`/incidents/${s.id}`} clickable label={`INC-${s.id}`} />
              : <Chip key={i} size="small" variant="outlined" label={s.title ?? String(s.id)} />)}
          </Stack>
        )}
        {message.suggested_actions?.map((a) => <Typography key={a} variant="caption" component="div" sx={{ mt: 0.5 }}>→ {a}</Typography>)}
      </Paper>
    </Box>
  );
}

export function ChatPage() {
  const [sessionId, setSessionId] = useState<string | undefined>(() => sessionStorage.getItem(SESSION_KEY) ?? undefined);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [llmUsed, setLlmUsed] = useState<boolean | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    chatApi.history(sessionId).then((h) => setMessages(h)).catch(() => sessionStorage.removeItem(SESSION_KEY));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { bottom.current?.scrollIntoView?.({ behavior: "smooth" }); }, [messages]);

  const send = useMutation({
    mutationFn: (text: string) => chatApi.send(text, sessionId),
    onSuccess: (resp: ChatResponse) => {
      sessionStorage.setItem(SESSION_KEY, resp.session_id);
      setSessionId(resp.session_id);
      setLlmUsed(resp.llm_used);
      setMessages((m) => [...m, { role: "assistant", content: resp.answer, sources: resp.sources, suggested_actions: resp.suggested_actions }]);
    },
  });

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || send.isPending) return;
    setMessages((m) => [...m, { role: "user", content: trimmed }]);
    setInput("");
    send.mutate(trimmed);
  };
  const onSubmit = (e: FormEvent) => { e.preventDefault(); submit(input); };

  return (
    <>
      <PageHeader title="AI assistant" subtitle="Ask about incidents, root causes, runbooks and approvals. The assistant is read-only." />
      {llmUsed === false && <Alert severity="info" sx={{ mb: 2 }}>No LLM is configured: answers are assembled from platform data without generative text.</Alert>}
      <Paper variant="outlined" sx={{ p: 2, height: "58vh", overflowY: "auto", mb: 2 }} aria-live="polite">
        {messages.length === 0 && (
          <Stack spacing={1} alignItems="flex-start">
            <Typography color="text.secondary">Try one of these:</Typography>
            {SUGGESTIONS.map((s) => <Chip key={s} label={s} onClick={() => submit(s)} clickable />)}
          </Stack>
        )}
        {messages.map((m, i) => <Bubble key={i} message={m} />)}
        {send.isPending && <Typography variant="caption" color="text.secondary">Assistant is thinking…</Typography>}
        {send.error && <Alert severity="error">{errorMessage(send.error)}</Alert>}
        <div ref={bottom} />
      </Paper>
      <Box component="form" onSubmit={onSubmit} sx={{ display: "flex", gap: 1 }}>
        <TextField fullWidth size="small" placeholder="Ask the copilot…" value={input} onChange={(e) => setInput(e.target.value)} inputProps={{ maxLength: 4000, "aria-label": "Message" }} />
        <IconButton type="submit" color="primary" aria-label="Send" disabled={!input.trim() || send.isPending}><SendIcon /></IconButton>
      </Box>
      <Link component="button" variant="caption" onClick={() => { sessionStorage.removeItem(SESSION_KEY); setSessionId(undefined); setMessages([]); }} sx={{ mt: 1 }}>New conversation</Link>
    </>
  );
}

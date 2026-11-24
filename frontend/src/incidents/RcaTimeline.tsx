import { Box, Divider, Stack, Typography } from "@mui/material";

import type { TimelineItem } from "../types";
import { formatDateTime } from "../utils/format";

const COLORS: Record<string, string> = {
  critical: "#d32f2f", error: "#ed6c02", warning: "#f9a825", info: "#0288d1",
};

/** Vertical RCA timeline: signals (metrics/logs/traces/deployments/alerts) interleaved with workflow steps. */
export function RcaTimeline({ items }: { items: TimelineItem[] }) {
  if (items.length === 0) return <Typography color="text.secondary">No timeline recorded yet.</Typography>;
  return (
    <Stack component="ol" sx={{ listStyle: "none", m: 0, p: 0 }} aria-label="RCA timeline">
      {items.map((item, idx) => {
        const color = item.source === "workflow" ? "#7b1fa2" : COLORS[item.severity ?? "info"] ?? COLORS.info;
        return (
          <Box component="li" key={`${item.timestamp}-${idx}`} sx={{ display: "flex", gap: 2, minHeight: 52 }}>
            <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <Box sx={{ width: 12, height: 12, borderRadius: "50%", bgcolor: color, mt: 0.6 }} />
              {idx < items.length - 1 && <Divider orientation="vertical" flexItem sx={{ flexGrow: 1, my: 0.5 }} />}
            </Box>
            <Box sx={{ pb: 1.5 }}>
              <Typography variant="caption" color="text.secondary">
                {formatDateTime(item.timestamp)}
                {item.service ? ` · ${item.service}` : ""}
                {item.stage ? ` · ${item.stage}` : item.type ? ` · ${item.type}` : ""}
              </Typography>
              <Typography variant="body2">{item.description}</Typography>
            </Box>
          </Box>
        );
      })}
    </Stack>
  );
}

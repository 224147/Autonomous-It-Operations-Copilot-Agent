import { Box, CircularProgress, Typography } from "@mui/material";

/** Circular confidence indicator for RCA (0..1). */
export function ConfidenceGauge({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const color = pct >= 85 ? "success" : pct >= 60 ? "warning" : "error";
  return (
    <Box sx={{ position: "relative", display: "inline-flex" }} role="img" aria-label={`Confidence ${pct}%`}>
      <CircularProgress variant="determinate" value={pct} size={88} thickness={5} color={color} />
      <Box sx={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
        <Typography variant="h6" component="span">{pct}%</Typography>
        <Typography variant="caption" color="text.secondary">confidence</Typography>
      </Box>
    </Box>
  );
}

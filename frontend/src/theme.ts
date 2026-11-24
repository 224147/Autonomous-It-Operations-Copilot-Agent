import { createTheme, type Theme } from "@mui/material";

import type { ThemeMode } from "./store/uiSlice";

export const buildTheme = (mode: ThemeMode): Theme =>
  createTheme({
    palette: { mode, primary: { main: mode === "dark" ? "#90caf9" : "#1a4fa0" }, background: mode === "dark" ? { default: "#0f1318", paper: "#171c23" } : { default: "#f4f6f9" } },
    shape: { borderRadius: 10 },
    typography: { h4: { fontSize: "1.7rem", fontWeight: 600 } },
  });

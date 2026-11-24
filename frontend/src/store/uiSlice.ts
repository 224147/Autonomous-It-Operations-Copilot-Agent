import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type { LiveEvent } from "../types";

export type ThemeMode = "light" | "dark";
const THEME_KEY = "copilot.theme";
const MAX_FEED = 60;

export interface Toast {
  id: string;
  severity: "info" | "warning" | "error" | "success";
  message: string;
}

export interface UiState {
  themeMode: ThemeMode;
  feed: LiveEvent[];
  toasts: Toast[];
  socketConnected: boolean;
}

const stored = typeof localStorage !== "undefined" ? localStorage.getItem(THEME_KEY) : null;

const initialState: UiState = {
  themeMode: stored === "dark" ? "dark" : "light",
  feed: [],
  toasts: [],
  socketConnected: false,
};

const slice = createSlice({
  name: "ui",
  initialState,
  reducers: {
    toggleTheme(state) {
      state.themeMode = state.themeMode === "light" ? "dark" : "light";
      localStorage.setItem(THEME_KEY, state.themeMode);
    },
    eventReceived(state, action: PayloadAction<LiveEvent>) {
      state.feed = [action.payload, ...state.feed].slice(0, MAX_FEED);
    },
    toastPushed(state, action: PayloadAction<Toast>) {
      state.toasts = [...state.toasts, action.payload].slice(-3);
    },
    toastDismissed(state, action: PayloadAction<string>) {
      state.toasts = state.toasts.filter((t) => t.id !== action.payload);
    },
    socketStatus(state, action: PayloadAction<boolean>) {
      state.socketConnected = action.payload;
    },
  },
});

export const { toggleTheme, eventReceived, toastPushed, toastDismissed, socketStatus } = slice.actions;
export default slice.reducer;

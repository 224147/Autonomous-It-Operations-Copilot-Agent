import { createAsyncThunk, createSlice, type PayloadAction } from "@reduxjs/toolkit";

import { authApi } from "../api/endpoints";
import { REFRESH_KEY, entraLoginRequest, errorMessage, loginRequest, refreshAccessToken } from "../api/client";
import type { TokenPair, User } from "../types";

export interface AuthState {
  user: User | null;
  accessToken: string | null;
  status: "idle" | "loading" | "authenticated" | "anonymous";
  error: string | null;
}

const initialState: AuthState = { user: null, accessToken: null, status: "idle", error: null };

export const storeRefreshToken = (token: string | null): void => {
  if (token) sessionStorage.setItem(REFRESH_KEY, token);
  else sessionStorage.removeItem(REFRESH_KEY);
};

const finishLogin = async (tokens: TokenPair) => {
  storeRefreshToken(tokens.refresh_token);
  return tokens.access_token;
};

export const login = createAsyncThunk<string, { email: string; password: string }, { rejectValue: string }>(
  "auth/login",
  async ({ email, password }, { rejectWithValue }) => {
    try {
      return await finishLogin(await loginRequest(email, password));
    } catch (err) {
      return rejectWithValue(errorMessage(err));
    }
  },
);

export const loginWithEntra = createAsyncThunk<string, string, { rejectValue: string }>(
  "auth/loginWithEntra",
  async (idToken, { rejectWithValue }) => {
    try {
      return await finishLogin(await entraLoginRequest(idToken));
    } catch (err) {
      return rejectWithValue(errorMessage(err));
    }
  },
);

/** On page load: exchange the stored refresh token for a new session (if any). */
export const restoreSession = createAsyncThunk<string | null>("auth/restore", async () => refreshAccessToken());

export const loadProfile = createAsyncThunk<User>("auth/profile", async () => authApi.me());

const slice = createSlice({
  name: "auth",
  initialState,
  reducers: {
    tokensUpdated(state, action: PayloadAction<string>) {
      state.accessToken = action.payload;
    },
    loggedOut(state) {
      Object.assign(state, { ...initialState, status: "anonymous" });
      storeRefreshToken(null);
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(login.pending, (s) => { s.status = "loading"; s.error = null; })
      .addCase(login.fulfilled, (s, a) => { s.accessToken = a.payload; })
      .addCase(login.rejected, (s, a) => { s.status = "anonymous"; s.error = a.payload ?? "Login failed"; })
      .addCase(loginWithEntra.pending, (s) => { s.status = "loading"; s.error = null; })
      .addCase(loginWithEntra.fulfilled, (s, a) => { s.accessToken = a.payload; })
      .addCase(loginWithEntra.rejected, (s, a) => { s.status = "anonymous"; s.error = a.payload ?? "SSO login failed"; })
      .addCase(restoreSession.fulfilled, (s, a) => {
        if (a.payload) s.accessToken = a.payload;
        else s.status = "anonymous";
      })
      .addCase(restoreSession.rejected, (s) => { s.status = "anonymous"; })
      .addCase(loadProfile.fulfilled, (s, a) => { s.user = a.payload; s.status = "authenticated"; })
      .addCase(loadProfile.rejected, (s) => { s.status = "anonymous"; s.accessToken = null; });
  },
});

export const { tokensUpdated, loggedOut } = slice.actions;
export default slice.reducer;

import { configureStore } from "@reduxjs/toolkit";
import { useDispatch, useSelector, type TypedUseSelectorHook } from "react-redux";

import { REFRESH_KEY, configureAuthBridge } from "../api/client";
import authReducer, { loggedOut, storeRefreshToken, tokensUpdated } from "./authSlice";
import uiReducer from "./uiSlice";

export const makeStore = () => configureStore({ reducer: { auth: authReducer, ui: uiReducer } });

export const store = makeStore();
export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;

export const useAppDispatch: () => AppDispatch = useDispatch;
export const useAppSelector: TypedUseSelectorHook<RootState> = useSelector;

configureAuthBridge({
  getAccessToken: () => store.getState().auth.accessToken,
  getRefreshToken: () => sessionStorage.getItem(REFRESH_KEY),
  onTokens: (tokens) => {
    storeRefreshToken(tokens.refresh_token);
    store.dispatch(tokensUpdated(tokens.access_token));
  },
  onLogout: () => store.dispatch(loggedOut()),
});

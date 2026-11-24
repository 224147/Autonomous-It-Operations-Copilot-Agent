import { useEffect, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { Spinner } from "../components/Common";
import { useRole } from "../hooks/useRole";
import { useAppDispatch, useAppSelector } from "../store";
import { loadProfile, restoreSession } from "../store/authSlice";
import type { Role } from "../types";

/** Restores the session on load, fetches the profile once a token exists, and redirects anonymous users to /login. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const dispatch = useAppDispatch();
  const location = useLocation();
  const { status, accessToken, user } = useAppSelector((s) => s.auth);

  useEffect(() => {
    if (status === "idle") void dispatch(restoreSession());
  }, [status, dispatch]);

  useEffect(() => {
    if (accessToken && !user && status !== "authenticated") void dispatch(loadProfile());
  }, [accessToken, user, status, dispatch]);

  if (status === "anonymous") return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (!user) return <Spinner />;
  return <>{children}</>;
}

export function RequireRole({ minimum, children }: { minimum: Role; children: ReactNode }) {
  const { has } = useRole();
  return has(minimum) ? <>{children}</> : <Navigate to="/" replace />;
}

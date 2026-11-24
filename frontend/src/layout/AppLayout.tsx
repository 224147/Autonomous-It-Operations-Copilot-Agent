import AnalyticsIcon from "@mui/icons-material/Insights";
import AlertsIcon from "@mui/icons-material/NotificationsActive";
import ApprovalIcon from "@mui/icons-material/FactCheck";
import AuditIcon from "@mui/icons-material/History";
import ChatIcon from "@mui/icons-material/SmartToy";
import DashboardIcon from "@mui/icons-material/Dashboard";
import DarkIcon from "@mui/icons-material/DarkMode";
import DeployIcon from "@mui/icons-material/RocketLaunch";
import HealthIcon from "@mui/icons-material/MonitorHeart";
import IncidentIcon from "@mui/icons-material/ReportProblem";
import LightIcon from "@mui/icons-material/LightMode";
import MenuIcon from "@mui/icons-material/Menu";
import PeopleIcon from "@mui/icons-material/People";
import KnowledgeIcon from "@mui/icons-material/MenuBook";
import {
  AppBar, Badge, Box, Chip, Divider, Drawer, IconButton, List, ListItemButton, ListItemIcon, ListItemText, Menu, MenuItem,
  Snackbar, Alert, Toolbar, Tooltip, Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { authApi, remediationApi } from "../api/endpoints";
import { useLiveEvents } from "../hooks/useLiveEvents";
import { useRole } from "../hooks/useRole";
import { useAppDispatch, useAppSelector } from "../store";
import { loggedOut } from "../store/authSlice";
import { toastDismissed, toggleTheme } from "../store/uiSlice";
import type { Role } from "../types";
import { ChangePasswordDialog } from "./ChangePasswordDialog";

const DRAWER = 232;

interface NavItem { to: string; label: string; icon: ReactNode; min?: Role; badge?: "approvals" }

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: <DashboardIcon /> },
  { to: "/incidents", label: "Incidents", icon: <IncidentIcon /> },
  { to: "/services", label: "Service health", icon: <HealthIcon /> },
  { to: "/alerts", label: "Alert center", icon: <AlertsIcon /> },
  { to: "/approvals", label: "Approvals", icon: <ApprovalIcon />, badge: "approvals" },
  { to: "/deployments", label: "Deployments", icon: <DeployIcon /> },
  { to: "/analytics", label: "Analytics", icon: <AnalyticsIcon /> },
  { to: "/knowledge", label: "Knowledge base", icon: <KnowledgeIcon /> },
  { to: "/chat", label: "AI assistant", icon: <ChatIcon />, min: "operator" },
  { to: "/audit", label: "Audit logs", icon: <AuditIcon />, min: "admin" },
  { to: "/users", label: "User management", icon: <PeopleIcon />, min: "admin" },
];

export function AppLayout() {
  useLiveEvents();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { has } = useRole();
  const { user } = useAppSelector((s) => s.auth);
  const { themeMode, toasts, socketConnected } = useAppSelector((s) => s.ui);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [pwdOpen, setPwdOpen] = useState(false);
  const pending = useQuery({ queryKey: ["approvals", "pending"], queryFn: () => remediationApi.approvals("pending"), refetchInterval: 60_000 });

  const logout = async () => {
    try {
      await authApi.logout(sessionStorage.getItem("copilot.refresh"));
    } finally {
      dispatch(loggedOut());
      navigate("/login");
    }
  };

  const drawer = (
    <Box role="navigation" aria-label="Main">
      <Toolbar><Typography variant="h6" noWrap>IT Ops Copilot</Typography></Toolbar>
      <Divider />
      <List>
        {NAV.filter((n) => !n.min || has(n.min)).map((item) => (
          <ListItemButton key={item.to} component={NavLink} to={item.to} end={item.to === "/"} onClick={() => setMobileOpen(false)}
            sx={{ "&.active": { bgcolor: "action.selected" } }}>
            <ListItemIcon>
              {item.badge === "approvals" ? <Badge color="warning" badgeContent={pending.data?.length ?? 0}>{item.icon}</Badge> : item.icon}
            </ListItemIcon>
            <ListItemText primary={item.label} />
          </ListItemButton>
        ))}
      </List>
    </Box>
  );

  return (
    <Box sx={{ display: "flex" }}>
      <AppBar position="fixed" sx={{ zIndex: (t) => t.zIndex.drawer + 1 }}>
        <Toolbar>
          <IconButton color="inherit" edge="start" aria-label="Open navigation" onClick={() => setMobileOpen(true)} sx={{ mr: 1, display: { sm: "none" } }}>
            <MenuIcon />
          </IconButton>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>Autonomous IT Operations Copilot</Typography>
          <Tooltip title={socketConnected ? "Live updates connected" : "Live updates disconnected"}>
            <Chip size="small" label={socketConnected ? "live" : "offline"} color={socketConnected ? "success" : "default"} sx={{ mr: 1 }} />
          </Tooltip>
          <IconButton color="inherit" aria-label="Toggle dark mode" onClick={() => dispatch(toggleTheme())}>
            {themeMode === "dark" ? <LightIcon /> : <DarkIcon />}
          </IconButton>
          <Chip label={`${user?.email ?? ""} · ${user?.role ?? ""}`} onClick={(e) => setMenuAnchor(e.currentTarget)} sx={{ ml: 1, color: "inherit", bgcolor: "rgba(255,255,255,0.15)" }} />
          <Menu anchorEl={menuAnchor} open={!!menuAnchor} onClose={() => setMenuAnchor(null)}>
            {user?.auth_provider === "local" && <MenuItem onClick={() => { setMenuAnchor(null); setPwdOpen(true); }}>Change password</MenuItem>}
            <MenuItem onClick={() => void logout()}>Sign out</MenuItem>
          </Menu>
        </Toolbar>
      </AppBar>
      <Drawer variant="temporary" open={mobileOpen} onClose={() => setMobileOpen(false)} sx={{ display: { xs: "block", sm: "none" }, "& .MuiDrawer-paper": { width: DRAWER } }}>
        {drawer}
      </Drawer>
      <Drawer variant="permanent" sx={{ display: { xs: "none", sm: "block" }, width: DRAWER, "& .MuiDrawer-paper": { width: DRAWER, boxSizing: "border-box" } }} open>
        {drawer}
      </Drawer>
      <Box component="main" sx={{ flexGrow: 1, p: { xs: 2, md: 3 }, minWidth: 0 }}>
        <Toolbar />
        <Outlet />
      </Box>
      {toasts.map((t) => (
        <Snackbar key={t.id} open autoHideDuration={6000} onClose={() => dispatch(toastDismissed(t.id))} anchorOrigin={{ vertical: "bottom", horizontal: "right" }}>
          <Alert severity={t.severity} variant="filled" onClose={() => dispatch(toastDismissed(t.id))}>{t.message}</Alert>
        </Snackbar>
      ))}
      <ChangePasswordDialog open={pwdOpen} onClose={() => setPwdOpen(false)} />
    </Box>
  );
}

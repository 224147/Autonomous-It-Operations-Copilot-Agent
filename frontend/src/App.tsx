import { CssBaseline, ThemeProvider } from "@mui/material";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense, useMemo } from "react";
import { Provider } from "react-redux";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { LoginPage } from "./auth/LoginPage";
import { RequireAuth, RequireRole } from "./auth/RequireAuth";
import { Spinner } from "./components/Common";
import { AppLayout } from "./layout/AppLayout";
import { store, useAppSelector } from "./store";
import { buildTheme } from "./theme";

const DashboardPage = lazy(() => import("./dashboard/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const ServiceHealthPage = lazy(() => import("./dashboard/ServiceHealthPage").then((m) => ({ default: m.ServiceHealthPage })));
const IncidentsPage = lazy(() => import("./incidents/IncidentsPage").then((m) => ({ default: m.IncidentsPage })));
const IncidentDetailPage = lazy(() => import("./incidents/IncidentDetailPage").then((m) => ({ default: m.IncidentDetailPage })));
const ApprovalsPage = lazy(() => import("./remediation/ApprovalsPage").then((m) => ({ default: m.ApprovalsPage })));
const DeploymentsPage = lazy(() => import("./remediation/DeploymentsPage").then((m) => ({ default: m.DeploymentsPage })));
const AnalyticsPage = lazy(() => import("./analytics/AnalyticsPage").then((m) => ({ default: m.AnalyticsPage })));
const ChatPage = lazy(() => import("./chatbot/ChatPage").then((m) => ({ default: m.ChatPage })));
const AlertsPage = lazy(() => import("./alerts/AlertsPage").then((m) => ({ default: m.AlertsPage })));
const KnowledgePage = lazy(() => import("./knowledge/KnowledgePage").then((m) => ({ default: m.KnowledgePage })));
const AuditPage = lazy(() => import("./audit/AuditPage").then((m) => ({ default: m.AuditPage })));
const UsersPage = lazy(() => import("./user-management/UsersPage").then((m) => ({ default: m.UsersPage })));

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false } },
});

function Themed() {
  const mode = useAppSelector((s) => s.ui.themeMode);
  const theme = useMemo(() => buildTheme(mode), [mode]);
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <Suspense fallback={<Spinner />}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
              <Route index element={<DashboardPage />} />
              <Route path="incidents" element={<IncidentsPage />} />
              <Route path="incidents/:id" element={<IncidentDetailPage />} />
              <Route path="services" element={<ServiceHealthPage />} />
              <Route path="alerts" element={<AlertsPage />} />
              <Route path="approvals" element={<ApprovalsPage />} />
              <Route path="deployments" element={<DeploymentsPage />} />
              <Route path="analytics" element={<AnalyticsPage />} />
              <Route path="knowledge" element={<KnowledgePage />} />
              <Route path="chat" element={<RequireRole minimum="operator"><ChatPage /></RequireRole>} />
              <Route path="audit" element={<RequireRole minimum="admin"><AuditPage /></RequireRole>} />
              <Route path="users" element={<RequireRole minimum="admin"><UsersPage /></RequireRole>} />
              <Route path="*" element={<DashboardPage />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ThemeProvider>
  );
}

export default function App() {
  return (
    <Provider store={store}>
      <QueryClientProvider client={queryClient}>
        <Themed />
      </QueryClientProvider>
    </Provider>
  );
}

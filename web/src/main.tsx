import "./index.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Route, Routes } from "react-router-dom";
import { Logo, Shell } from "./components/Shell";
import { Button, Input, Providers } from "./components/ui";
import { ApiError, captureToken, onConnection, setToken, token } from "./lib/api";
import { AccountProvider, useAppState, useReadingPrefs } from "./lib/state";
import Dashboard from "./pages/Dashboard";

// Pages load when first opened, so Convo starts quickly (the Dashboard is part of the first load).
const Analytics = lazy(() => import("./pages/Analytics"));
const Automations = lazy(() => import("./pages/Automations"));
const Calendar = lazy(() => import("./pages/Calendar"));
const Library = lazy(() => import("./pages/Library"));
const Scheduled = lazy(() => import("./pages/Scheduled"));
const Studio = lazy(() => import("./pages/Studio"));
const Contacts = lazy(() => import("./pages/Contacts"));
const Conversations = lazy(() => import("./pages/Conversations"));
const Hello = lazy(() => import("./pages/Hello"));
const Phone = lazy(() => import("./pages/Phone"));
const Followups = lazy(() => import("./pages/Followups"));
const Reports = lazy(() => import("./pages/Reports"));
const Review = lazy(() => import("./pages/Review"));
const Strategist = lazy(() => import("./pages/Strategist"));
const Updates = lazy(() => import("./pages/Updates"));
const AccountGroups = lazy(() => import("./pages/Accounts").then((m) => ({ default: m.AccountGroups })));
const ConnectedAccounts = lazy(() => import("./pages/Accounts").then((m) => ({ default: m.ConnectedAccounts })));
const LaunchNetwork = lazy(() => import("./pages/Accounts").then((m) => ({ default: m.LaunchNetwork })));
const AutomationHistory = lazy(() => import("./pages/Activity").then((m) => ({ default: m.AutomationHistory })));
const Notifications = lazy(() => import("./pages/Activity").then((m) => ({ default: m.Notifications })));
const Collaborations = lazy(() => import("./pages/Growth").then((m) => ({ default: m.Collaborations })));
const EngagementAssistant = lazy(() => import("./pages/Growth").then((m) => ({ default: m.EngagementAssistant })));
const GrowthGoals = lazy(() => import("./pages/Growth").then((m) => ({ default: m.GrowthGoals })));
const OrganicIndependence = lazy(() => import("./pages/Growth").then((m) => ({ default: m.OrganicIndependence })));
const WeeklyMissions = lazy(() => import("./pages/Growth").then((m) => ({ default: m.WeeklyMissions })));
const AccountSettings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.AccountSettings })));
const AIPreferences = lazy(() => import("./pages/Settings").then((m) => ({ default: m.AIPreferences })));
const ApiConnections = lazy(() => import("./pages/Settings").then((m) => ({ default: m.ApiConnections })));
const Integrations = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Integrations })));

captureToken();

const qc = new QueryClient({
  defaultOptions: { queries: { retry: (n, e) => !(e instanceof ApiError && [0, 401, 404].includes(e.status)) && n < 2, staleTime: 2000, refetchOnWindowFocus: true } },
});

function Gate({ onOpen }: { onOpen: () => void }) {
  const [v, setV] = useState("");
  return (
    <div className="app-glow grid min-h-dvh place-items-center px-6">
      <form className="w-full max-w-sm rounded-3xl border border-line bg-surface p-7 shadow-[var(--shadow-pop)]" onSubmit={(e) => { e.preventDefault(); setToken(v); onOpen(); }}>
        <Logo /><h1 className="mt-4 text-[20px] font-semibold">Open Convo again</h1>
        <p className="mt-1 text-[13.5px] text-muted">This window lost its connection to Convo. Close it and open <b>Convo</b> from Applications: it opens already signed in.</p>
        <Input className="mt-5" type="password" autoFocus value={v} onChange={(e) => setV(e.target.value)} placeholder="Password" aria-label="Password" />
        <Button variant="primary" className="mt-3 w-full" size="lg">Open</Button>
      </form>
    </div>
  );
}

function App() {
  useReadingPrefs();
  const [, bump] = useState(0);
  const st = useAppState();
  useEffect(() => onConnection((k) => k === "auth" && bump((n) => n + 1)), []);
  if (!token() || (st.error instanceof ApiError && st.error.status === 401)) return <Gate onOpen={() => { qc.invalidateQueries(); bump((n) => n + 1); }} />;
  return (
    <Shell>
      <Suspense fallback={<div className="grid gap-4"><div className="skeleton h-10 w-64 rounded-xl" /><div className="skeleton h-72 rounded-2xl" /></div>}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/strategist" element={<Strategist />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="/accounts" element={<ConnectedAccounts />} />
        <Route path="/launch" element={<LaunchNetwork />} />
        <Route path="/groups" element={<AccountGroups />} />
        <Route path="/studio" element={<Studio />} />
        <Route path="/studio/:id" element={<Studio />} />
        <Route path="/library" element={<Library />} />
        <Route path="/calendar" element={<Calendar />} />
        <Route path="/scheduled" element={<Scheduled />} />
        <Route path="/hello" element={<Hello />} />
        <Route path="/review/hellos" element={<Review kind="hello" key="hello" />} />
        <Route path="/review/replies" element={<Review kind="reply" key="reply" />} />
        <Route path="/conversations" element={<Conversations />} />
        <Route path="/contacts" element={<Contacts />} />
        <Route path="/followups" element={<Followups />} />
        <Route path="/engagement" element={<EngagementAssistant />} />
        <Route path="/collaborations" element={<Collaborations />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/missions" element={<WeeklyMissions />} />
        <Route path="/goals" element={<GrowthGoals />} />
        <Route path="/independence" element={<OrganicIndependence />} />
        <Route path="/automations" element={<Automations />} />
        <Route path="/automation-history" element={<AutomationHistory />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/api-connections" element={<ApiConnections />} />
        <Route path="/ai" element={<AIPreferences />} />
        <Route path="/settings" element={<AccountSettings />} />
        <Route path="/updates" element={<Updates />} />
        <Route path="/phone" element={<Phone />} />
        <Route path="*" element={<Dashboard />} />
      </Routes>
      </Suspense>
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <Providers>
        <AccountProvider>
          <HashRouter><App /></HashRouter>
        </AccountProvider>
      </Providers>
    </QueryClientProvider>
  </StrictMode>,
);

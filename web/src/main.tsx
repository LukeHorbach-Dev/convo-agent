import "./index.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Route, Routes } from "react-router-dom";
import { Logo, Shell } from "./components/Shell";
import { Button, Input, Providers } from "./components/ui";
import { ApiError, captureToken, onConnection, setToken, token } from "./lib/api";
import { AccountProvider, useAppState, useReadingPrefs } from "./lib/state";
import { AccountGroups, ConnectedAccounts, LaunchNetwork } from "./pages/Accounts";
import { AutomationHistory, Notifications } from "./pages/Activity";
import Analytics from "./pages/Analytics";
import Automations from "./pages/Automations";
import { Collaborations, EngagementAssistant, GrowthGoals, OrganicIndependence, WeeklyMissions } from "./pages/Coming";
import Calendar from "./pages/Calendar";
import Library from "./pages/Library";
import Scheduled from "./pages/Scheduled";
import Studio from "./pages/Studio";
import Contacts from "./pages/Contacts";
import Conversations from "./pages/Conversations";
import Dashboard from "./pages/Dashboard";
import Hello from "./pages/Hello";
import Phone from "./pages/Phone";
import Followups from "./pages/Followups";
import Reports from "./pages/Reports";
import Review from "./pages/Review";
import { AccountSettings, AIPreferences, ApiConnections, Integrations } from "./pages/Settings";
import Strategist from "./pages/Strategist";
import Updates from "./pages/Updates";

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

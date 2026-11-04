// What every screen shares: Convo's live status (polled), the chosen account, and reading preferences.
import { useQuery } from "@tanstack/react-query";
import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { api } from "./api";

export type AppState = {
  version: string; you: string; practice: boolean; auto_send: boolean;
  job: { name: string | null; done: number; total: number; error: string | null };
  check: { running: boolean; at: string | null; summary: string | null };
  sender: { active: boolean; status: string; stopped: boolean; waiting_until: number | null; quiet_until: string | null };
  overnight: { count: number; since: number } | null;
  counts: Record<string, number>;
  settings: Record<string, any>;
  ai: { on: boolean; model: string; spend_total: number; spend_today: number; drafts: number; drafts_usd: number; web_steps: number;
        web_usd: number; eyes_model: string; other_usd: number; estimates: Record<string, { overnight: number; now: number }>; send_estimate: number };
  activity: { at: string; kind: string; text: string }[];
};

export function useAppState() {
  return useQuery({ queryKey: ["state"], queryFn: () => api<AppState>("/api/state"), refetchInterval: 4000 });
}

const AccountCtx = createContext<[string, (v: string) => void]>(["all", () => {}]);

const read = (k: string, d: string) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const write = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private window */ } };

export function AccountProvider({ children }: { children: ReactNode }) {
  const [acct, setAcct] = useState(() => read("convo.account", "all"));
  return <AccountCtx.Provider value={[acct, (v) => { setAcct(v); write("convo.account", v); }]}>{children}</AccountCtx.Provider>;
}
export const useAccount = () => useContext(AccountCtx);

export const prefs = { read, write };

export function useReadingPrefs() {
  const [scale, setScale] = useState(() => Number(read("convo.scale", "1")) || 1);
  const [easy, setEasy] = useState(() => read("convo.easy", "") === "1");
  useEffect(() => {
    document.documentElement.style.setProperty("--text-scale", String(scale));
    document.documentElement.classList.toggle("easy-read", easy);
    write("convo.scale", String(scale));
    write("convo.easy", easy ? "1" : "");
  }, [scale, easy]);
  return { scale, setScale, easy, setEasy };
}

// Talking to Convo on this Mac. The password ("token") arrives in the address when the app opens the window
// (#token=...), is kept for this window only, and goes with every request. Nothing secret lives in the page.

const KEY = "tok";

export function captureToken(): void {
  const m = location.hash.match(/token=([^&]+)/);
  if (m) {
    sessionStorage.setItem(KEY, decodeURIComponent(m[1]));
    history.replaceState(null, "", location.pathname + "#/");
  }
}

export const token = () => sessionStorage.getItem(KEY) || "";
export const setToken = (t: string) => sessionStorage.setItem(KEY, t.trim());

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Listener = (kind: "offline" | "auth") => void;
const listeners = new Set<Listener>();
export const onConnection = (l: Listener) => { listeners.add(l); return () => { listeners.delete(l); }; };

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  let r: Response;
  try {
    r = await fetch(path, {
      method: opts.method || "GET",
      headers: { "X-Agent-Token": token(), "content-type": "application/json" },
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch {
    listeners.forEach((l) => l("offline"));
    throw new ApiError(0, "Convo isn't running right now.");
  }
  if (r.status === 401) {
    listeners.forEach((l) => l("auth"));
    throw new ApiError(401, "Convo needs its password again.");
  }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError(r.status, (data && (data as any).detail) || `Something went wrong (${r.status})`);
  return data as T;
}

export const post = <T = any>(path: string, body: unknown = {}) => api<T>(path, { method: "POST", body });
export const patch = <T = any>(path: string, body: unknown = {}) => api<T>(path, { method: "PATCH", body });
export const del = <T = any>(path: string) => api<T>(path, { method: "DELETE" });

export async function download(path: string, filename: string): Promise<void> {
  const r = await fetch(path, { headers: { "X-Agent-Token": token() } });
  if (!r.ok) throw new ApiError(r.status, "Couldn't download that");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export async function isUp(): Promise<boolean> {
  try {
    return (await fetch("/api/health", { cache: "no-store" })).ok;
  } catch {
    return false;
  }
}

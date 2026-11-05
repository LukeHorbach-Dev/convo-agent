// Posts: ideas, drafts, files and their words for each platform (see assistant/content.py).
import { api, token } from "./api";

export type PlatformId = "instagram" | "tiktok" | "youtube";
export const PLATFORM: Record<PlatformId, { name: string; color: string; short: string }> = {
  instagram: { name: "Instagram", color: "#e1306c", short: "IG" },
  tiktok: { name: "TikTok", color: "#25f4ee", short: "TT" },
  youtube: { name: "YouTube", color: "#ff3d3d", short: "YT" },
};
export const PLATFORM_IDS = Object.keys(PLATFORM) as PlatformId[];

export type Kind = "idea" | "video" | "short" | "photo" | "carousel" | "text";
export type Status = "idea" | "draft" | "ready" | "scheduled" | "due" | "posted";
export type Version = { caption?: string; title?: string; description?: string; hashtags?: string[] };
export type Content = {
  id: number; title: string; kind: Kind; status: Status; notes: string; platforms: PlatformId[]; account_id: number | null;
  versions: Partial<Record<PlatformId, Version>>; media: string | null; media_type: string | null; thumb: string | null;
  media_url: string | null; thumb_url: string | null; scheduled_at: string | null; posted_at: string | null; post_url: string | null;
  created_at: string; updated_at: string; account?: { id: number; platform: PlatformId; handle: string } | null;
};

export const KINDS: [Kind, string][] = [["idea", "Idea"], ["video", "Video"], ["short", "Short video"], ["photo", "Photo"], ["carousel", "Carousel"], ["text", "Text post"]];
export const STATUS: Record<Status, { label: string; tone: "neutral" | "violet" | "cyan" | "good" | "warn" }> = {
  idea: { label: "Idea", tone: "neutral" }, draft: { label: "Draft", tone: "violet" }, ready: { label: "Ready", tone: "cyan" },
  scheduled: { label: "Scheduled", tone: "violet" }, due: { label: "Time to post", tone: "warn" }, posted: { label: "Posted", tone: "good" },
};

export const preview = (c: Content) => c.thumb_url || (c.media_type?.startsWith("image/") ? c.media_url : null);

/** The words to paste for one platform: caption (or title + description) and hashtags. */
export function pasteText(v: Version | undefined, p: PlatformId): string {
  if (!v) return "";
  const tags = (v.hashtags || []).map((t) => `#${t}`).join(" ");
  if (p === "youtube") return [v.title, v.description, tags].filter(Boolean).join("\n\n");
  return [v.caption, tags].filter(Boolean).join("\n\n");
}

/** Upload a file with progress (fetch can't report upload progress). */
export function uploadFile(id: number, file: Blob, filename: string, role: "media" | "thumb", onProgress?: (pct: number) => void): Promise<Content> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", `/api/hub/content/${id}/upload?filename=${encodeURIComponent(filename)}&role=${role}`);
    x.setRequestHeader("X-Agent-Token", token());
    x.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100));
    x.onload = () => {
      let body: any = null;
      try { body = JSON.parse(x.responseText); } catch { /* not json */ }
      if (x.status >= 200 && x.status < 300) resolve(body);
      else reject(new Error(body?.detail || `Upload failed (${x.status})`));
    };
    x.onerror = () => reject(new Error("Upload failed: is Convo running?"));
    x.send(file);
  });
}

/** A still from a video (about a second in), as a JPEG, made in the browser: no extra software on the Mac. */
export function videoStill(file: File): Promise<Blob | null> {
  return new Promise((resolve) => {
    const v = document.createElement("video");
    const url = URL.createObjectURL(file);
    const done = (b: Blob | null) => { URL.revokeObjectURL(url); resolve(b); };
    v.muted = true; v.playsInline = true; v.preload = "metadata"; v.src = url;
    v.onloadeddata = () => { v.currentTime = Math.min(1, (v.duration || 2) / 2); };
    v.onseeked = () => {
      const w = Math.min(720, v.videoWidth || 720), h = Math.round(w * ((v.videoHeight || 1280) / (v.videoWidth || 720)));
      const c = document.createElement("canvas"); c.width = w; c.height = h;
      c.getContext("2d")?.drawImage(v, 0, 0, w, h);
      c.toBlob((b) => done(b), "image/jpeg", 0.82);
    };
    v.onerror = () => done(null);
    setTimeout(() => done(null), 15000);
  });
}

export const getContent = (id: number) => api<Content>(`/api/hub/content/${id}`);

/** "2026-11-05T18:00" in this Mac's time, for <input type="datetime-local">. */
export function localInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export const when = (iso: string | null) => iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";

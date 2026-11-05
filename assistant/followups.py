"""Follow-ups: who's waiting on you, hellos nobody answered, and reminders you set yourself.

Three kinds, all worked out from what Convo already knows (your Instagram downloads), and none of them sends
anything: each one is a nudge for you to decide.
  waiting   they wrote last, a couple of days ago or more, and no reply is approved for them
  no_reply  a hello went out a week or more ago and they haven't written back (as of your newest download)
  reminder  "remind me about @maya on Friday: ask how the show went", set from a person's panel

"Snooze" hides one for a while, "Done" hides it for good. A new message in a chat brings it back (it's a new
follow-up), and so does a reminder's date coming round.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .store import Store, now_iso

WAITING_DAYS = 2           # they wrote this long ago and you haven't answered
NO_REPLY_DAYS = 7          # your hello went out this long ago with no answer
LOOK_BACK_DAYS = 60        # older than this isn't a follow-up any more, it's history


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def followups(st: Store, now: datetime | None = None, waiting_days: int = WAITING_DAYS, no_reply_days: int = NO_REPLY_DAYS,
              never: set[str] | frozenset = frozenset()) -> dict:
    """Never suggests anyone Convo mustn't contact: people marked "careful" (under 19, asking for money and the
    like), anyone on your never-message list, and chats you chose to skip."""
    now = now or datetime.now(timezone.utc)
    ms = lambda dt: int(dt.timestamp() * 1000)
    hidden = {r["key"]: r for r in st.all("SELECT * FROM followups")}

    def visible(key: str) -> bool:
        h = hidden.get(key)
        if not h:
            return True
        if h["done_at"]:
            return False
        until = _parse(h["snoozed_until"])
        return not until or until <= now

    careful = "EXISTS (SELECT 1 FROM people p WHERE p.username = {col} AND p.concern = 'high')"
    items = []
    for t in st.all(f"""SELECT t.id, t.username, t.other_name, t.title, t.last_at,
            (SELECT text FROM messages m WHERE m.thread_id = t.id ORDER BY at DESC LIMIT 1) AS last_text,
            EXISTS (SELECT 1 FROM drafts d WHERE d.thread_id = t.id AND d.status = 'draft' AND d.text != '') AS has_draft
        FROM threads t
        WHERE t.is_group = 0 AND t.is_request = 0 AND t.last_from_me = 0 AND t.last_at < ? AND t.last_at > ?
          AND NOT {careful.format(col="t.username")}
          AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.thread_id = t.id AND d.status IN ('approved', 'sending', 'sent', 'skipped')
                          AND COALESCE(d.replying_at, 0) >= t.last_at)
          AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.thread_id = t.id AND d.status IN ('skipped', 'unsure')
                          AND d.replying_at IS NULL AND d.updated_at >= datetime(t.last_at / 1000, 'unixepoch'))
        ORDER BY t.last_at DESC LIMIT 200""", (ms(now - timedelta(days=waiting_days)), ms(now - timedelta(days=LOOK_BACK_DAYS)))):
        key = f"waiting:{t['id']}:{t['last_at']}"
        if (t["username"] or "").lower() in never:
            continue
        if visible(key):
            since = datetime.fromtimestamp(t["last_at"] / 1000, timezone.utc)
            items.append({"key": key, "kind": "waiting", "username": t["username"], "name": t["other_name"] or t["title"] or t["username"],
                          "thread_id": t["id"], "since": _iso(since), "days": (now - since).days, "text": t["last_text"],
                          "has_draft": bool(t["has_draft"])})

    last_import = _parse((st.get("last_import") or {}).get("at"))
    unknown = 0
    for d in st.all(f"""SELECT d.id, d.username, d.other_name, d.sent_at, d.text FROM drafts d
        WHERE d.kind = 'hello' AND d.status = 'sent' AND d.sent_at < ? AND d.sent_at > ?
          AND NOT {careful.format(col="d.username")}
          AND NOT EXISTS (SELECT 1 FROM threads t JOIN messages m ON m.thread_id = t.id
                          WHERE t.username = d.username AND m.from_me = 0 AND m.at > CAST(strftime('%s', d.sent_at) AS INTEGER) * 1000)
        ORDER BY d.sent_at DESC LIMIT 500""", (_iso(now - timedelta(days=no_reply_days)), _iso(now - timedelta(days=LOOK_BACK_DAYS)))):
        sent = _parse(d["sent_at"])
        if (d["username"] or "").lower() in never:
            continue
        if not last_import or not sent or last_import <= sent:
            unknown += 1                       # no download since it went out: can't tell yet if they answered
            continue
        key = f"no_reply:{d['id']}"
        if visible(key):
            items.append({"key": key, "kind": "no_reply", "username": d["username"], "name": d["other_name"] or d["username"],
                          "since": d["sent_at"], "days": (now - sent).days, "text": d["text"]})

    upcoming = []
    for r in st.all("SELECT * FROM reminders WHERE done_at IS NULL ORDER BY due_at"):
        due = _parse(r["due_at"])
        row = {"key": f"reminder:{r['id']}", "kind": "reminder", "id": r["id"], "username": r["username"], "name": r["username"],
               "since": r["due_at"], "days": (now - due).days if due else 0, "text": r["note"]}
        if due and due <= now:
            if visible(row["key"]):
                items.append(row)
        else:
            upcoming.append(row)

    order = {"reminder": 0, "waiting": 1, "no_reply": 2}
    items.sort(key=lambda i: (order[i["kind"]], i["since"] or ""), reverse=False)
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in order}
    return {"items": items, "upcoming": upcoming, "counts": counts, "total": len(items), "unknown_hellos": unknown,
            "as_of": last_import and _iso(last_import), "waiting_days": waiting_days, "no_reply_days": no_reply_days}


def remind_due(st: Store, notify) -> int:
    """A Mac notification once for each reminder whose day has come. Returns how many."""
    told = set(st.get("reminders_told") or [])
    due = [r for r in st.all("SELECT * FROM reminders WHERE done_at IS NULL AND due_at <= ?", (now_iso(),)) if r["id"] not in told]
    for r in due:
        notify("Convo reminder", (f"@{r['username']}: " if r["username"] else "") + r["note"])
        told.add(r["id"])
    if due:
        st.set("reminders_told", sorted(told)[-500:])
    return len(due)


class SnoozeBody(BaseModel):
    days: int = 3


class ReminderBody(BaseModel):
    username: str | None = None
    note: str
    due_at: str                      # ISO date or date-time


def register(api: FastAPI, a, auth) -> None:
    st: Store = a.store
    dep = [Depends(auth)]

    @api.get("/api/hub/followups", dependencies=dep)
    def list_followups():
        return followups(st, never={u.lower() for u in a.never()})

    @api.post("/api/hub/followups/{key}/snooze", dependencies=dep)
    def snooze(key: str, body: SnoozeBody):
        days = max(1, min(body.days, 90))
        until = _iso(datetime.now(timezone.utc) + timedelta(days=days))
        st.execute("INSERT INTO followups(key, snoozed_until) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET snoozed_until = excluded.snoozed_until, "
                   "done_at = NULL", (key, until))
        return {"ok": True, "until": until}

    @api.post("/api/hub/followups/{key}/done", dependencies=dep)
    def done(key: str):
        if key.startswith("reminder:"):
            st.execute("UPDATE reminders SET done_at = ? WHERE id = ?", (now_iso(), int(key.split(":", 1)[1])))
        st.execute("INSERT INTO followups(key, done_at) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET done_at = excluded.done_at", (key, now_iso()))
        return {"ok": True}

    @api.post("/api/hub/followups/{key}/undo", dependencies=dep)
    def undo(key: str):
        if key.startswith("reminder:"):
            st.execute("UPDATE reminders SET done_at = NULL WHERE id = ?", (int(key.split(":", 1)[1]),))
        st.execute("DELETE FROM followups WHERE key = ?", (key,))
        return {"ok": True}

    @api.post("/api/hub/reminders", dependencies=dep)
    def add_reminder(body: ReminderBody):
        note = body.note.strip()[:500]
        if not note:
            raise HTTPException(400, "Write what to remind you about.")
        due = _parse(body.due_at if "T" in body.due_at else body.due_at + "T09:00:00")
        if due is None:
            raise HTTPException(400, "Pick a date.")
        if due.tzinfo is None:
            due = due.astimezone()              # the Mac's own time zone
        user = (body.username or "").strip().lstrip("@") or None
        rid = st.execute("INSERT INTO reminders(username, note, due_at, created_at) VALUES (?, ?, ?, ?)",
                         (user, note, _iso(due), now_iso())).lastrowid
        st.log("info", f"Reminder set for {due.strftime('%a %d %b')}{f' about @{user}' if user else ''}.")
        return {"id": rid, "due_at": _iso(due)}

    @api.delete("/api/hub/reminders/{rid}", dependencies=dep)
    def delete_reminder(rid: int):
        st.execute("DELETE FROM reminders WHERE id = ?", (rid,))
        st.execute("DELETE FROM followups WHERE key = ?", (f"reminder:{rid}",))
        return {"ok": True}

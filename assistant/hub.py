"""The Command Centre's data: everything the new interface shows, read from the same store Convo already uses.

Nothing here invents a number. Every figure comes from a stored fact (a download, something Convo sent, a platform
connection), and each says where it came from (`source`) and how fresh it is (`as_of`). Figures that need a
platform connection that isn't set up yet come back as None with the reason, so the screen can say so.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .platforms import MAX_LAUNCH_ACCOUNTS, PLATFORMS, ROLES, clean_handle, profile_url
from .store import Store, iso_ago, now_iso


# ---------------------------------------------------------------------------------------------- accounts
def ensure_main_account(store: Store, username: str) -> int:
    """Luke's own Instagram account, which Convo has always worked for. Created once, from what Convo already knows."""
    row = store.one("SELECT id FROM accounts WHERE platform = 'instagram' AND role = 'main' AND handle = ?", (username.lower(),))
    if row:
        return row["id"]
    li = store.get("last_import") or {}
    store.execute("INSERT OR IGNORE INTO accounts(platform, handle, display_name, role, purpose, connection, status, "
                  "status_note, created_at, updated_at) VALUES ('instagram', ?, ?, 'main', ?, ?, ?, ?, ?, ?)",
                  (username.lower(), li.get("your_name"), "My personal brand", "download,browser",
                   "connected" if li else "not_connected",
                   "Instagram downloads and Convo's own browser" if li else "Load an Instagram download to start",
                   now_iso(), now_iso()))
    aid = store.scalar("SELECT id FROM accounts WHERE platform = 'instagram' AND handle = ?", (username.lower(),))
    # The numbers from the last download, as the first point of the follower history.
    if li.get("followers") is not None:
        record_snapshot(store, aid, li.get("at") or now_iso(), "download", li.get("followers"), li.get("following"))
    return aid


def record_snapshot(store: Store, account_id: int, at: str, source: str, followers: int | None, following: int | None,
                    posts: int | None = None, extra: dict | None = None) -> None:
    store.execute("INSERT OR REPLACE INTO snapshots(account_id, at, source, followers, following, posts, extra) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (account_id, at, source, followers, following, posts, json.dumps(extra or {})))


def after_download(store: Store, username: str, summary: dict) -> None:
    """Called after a download is loaded: keep the follower numbers as history, and mark the account as connected."""
    aid = ensure_main_account(store, username)
    if summary.get("followers") is not None or summary.get("following") is not None:
        record_snapshot(store, aid, summary["at"], "download", summary.get("followers"), summary.get("following"))
    store.execute("UPDATE accounts SET status = 'connected', status_note = 'Instagram downloads and Convo''s own browser', "
                  "display_name = COALESCE(display_name, ?), updated_at = ? WHERE id = ?",
                  (summary.get("your_name"), now_iso(), aid))


def account_row(store: Store, r: dict) -> dict:
    snaps = store.all("SELECT at, followers, following, source FROM snapshots WHERE account_id = ? ORDER BY at", (r["id"],))
    last = snaps[-1] if snaps else None
    week = _change(snaps, days=7)
    g = store.one("SELECT name, color FROM account_groups WHERE id = ?", (r["group_id"],)) if r.get("group_id") else None
    return {**r, "platform_name": PLATFORMS.get(r["platform"], {}).get("name", r["platform"]),
            "url": profile_url(r["platform"], r["handle"]) if r["platform"] in PLATFORMS else None,
            "group": g, "followers": last["followers"] if last else None, "following": last["following"] if last else None,
            "as_of": last["at"] if last else None, "source": last["source"] if last else None,
            "followers_change_7d": week, "history_points": len(snaps)}


def _change(snaps: list[dict], days: int) -> int | None:
    """Follower change over the last `days`, from two real measurements at least `days` apart; None if there aren't."""
    have = [s for s in snaps if s["followers"] is not None]
    if len(have) < 2:
        return None
    last = have[-1]
    cutoff = (datetime.fromisoformat(last["at"]) - timedelta(days=days)).isoformat()
    before = [s for s in have if s["at"] <= cutoff]
    return last["followers"] - before[-1]["followers"] if before else None


# ---------------------------------------------------------------------------------------------- numbers
def _day(iso_or_ms) -> str:
    if isinstance(iso_or_ms, (int, float)):
        return datetime.fromtimestamp(iso_or_ms / 1000, timezone.utc).strftime("%Y-%m-%d")
    return str(iso_or_ms)[:10]


def activity_series(store: Store, days: int = 30) -> list[dict]:
    """Per day: messages Convo sent (hellos and replies) and messages people sent you (from downloads)."""
    start = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    series = {(start + timedelta(days=i)).isoformat(): {"sent_hellos": 0, "sent_replies": 0, "received": 0} for i in range(days)}
    for r in store.all("SELECT kind, sent_at FROM drafts WHERE status = 'sent' AND sent_at >= ?", (start.isoformat(),)):
        d = _day(r["sent_at"])
        if d in series:
            series[d]["sent_hellos" if r["kind"] == "hello" else "sent_replies"] += 1
    start_ms = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp() * 1000)
    for r in store.all("SELECT at FROM messages m JOIN threads t ON t.id = m.thread_id WHERE m.from_me = 0 AND m.at >= ? "
                       "AND t.is_group = 0", (start_ms,)):
        d = _day(r["at"])
        if d in series:
            series[d]["received"] += 1
    return [{"date": d, **v} for d, v in series.items()]


def hello_stats(store: Store) -> dict:
    """The Hello Message queue and how hellos are doing. Reply rate: of the hellos Convo sent, how many people wrote
    back afterwards (as far as the downloads show)."""
    c = lambda sql, *p: store.scalar(sql, p) or 0
    sent = store.all("SELECT username, sent_at FROM drafts WHERE kind = 'hello' AND status = 'sent' AND username IS NOT NULL")
    replied = 0
    for h in sent:
        ms = int(datetime.fromisoformat(h["sent_at"]).timestamp() * 1000)
        if store.scalar("SELECT 1 FROM messages m JOIN threads t ON t.id = m.thread_id WHERE t.username = ? AND m.from_me = 0 "
                        "AND m.at > ? LIMIT 1", (h["username"], ms)):
            replied += 1
    return {
        "waiting_approval": c("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'draft'"),
        "approved": c("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'approved'"),
        "sent": len(sent), "replied": replied,
        "reply_rate": (replied / len(sent)) if sent else None,
        "skipped": c("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status IN ('skipped', 'failed', 'unsure')"),
        "sent_7d": c("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'sent' AND sent_at >= ?", iso_ago(days=7)),
    }


def next_steps(store: Store, a) -> list[dict]:
    """What's worth doing now, worked out from the data (no AI): the dashboard's recommendations and tasks."""
    c = lambda sql, *p: store.scalar(sql, p) or 0
    out = []
    li = store.get("last_import")
    if not li:
        out.append({"kind": "task", "title": "Load your first Instagram download", "detail": "Convo reads it to know your followers and every chat.",
                    "action": "load_download"})
    else:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(li["at"])).days
        if age >= 2:
            out.append({"kind": "task", "title": f"Your last download is {age} days old",
                        "detail": "Request a new one in Instagram so replies from the last few days show up.", "action": "help_download"})
    n = c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'draft'")
    if n:
        look = c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'draft' AND (json_extract(context, '$.needs_look') "
                 "OR json_extract(context, '$.manual'))")
        out.append({"kind": "task", "title": f"{n} {'reply' if n == 1 else 'replies'} to check",
                    "detail": f"{look} need a proper look." if look else "All ready for a quick read.", "action": "open_replies"})
    hellos = c("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'draft'")
    if hellos:
        out.append({"kind": "task", "title": f"{hellos:,} hellos waiting for your approval",
                    "detail": "They stay in the queue until you approve them. Nothing is sent without you.", "action": "open_hellos"})
    approved = c("SELECT COUNT(*) FROM drafts WHERE status = 'approved'")
    if approved and not a.sender.active:
        out.append({"kind": "task", "title": f"{approved} approved and ready to send", "detail": "Send them, or set a daily send time.",
                    "action": "send"})
    if a.manual:
        out.append({"kind": "setup", "title": "Add your AI key", "detail": "Writing replies, Ask and sending need it.", "action": "settings_ai"})
    for p, info in PLATFORMS.items():
        if not store.scalar("SELECT 1 FROM accounts WHERE platform = ? AND connection LIKE '%api%'", (p,)):
            out.append({"kind": "later", "title": f"Connect {info['name']}", "detail": f"Arrives in stage {info['stage']}: {info['official']['connect']}",
                        "action": "integrations"})
    return out


def integrations(store: Store, a) -> list[dict]:
    """What's connected, what isn't, and what each needs. Statuses come from real checks, never assumed."""
    li = store.get("last_import")
    check = a.check.get("report") or ""
    items = [
        {"id": "anthropic", "name": "Claude (Anthropic API)", "status": "connected" if not a.manual else "not_configured",
         "detail": f"Writes replies with {a.s.llm.model}; uses Instagram with {a.s.eyes.model}." if not a.manual
                   else "Paste your API key in AI Preferences.", "stage": 0},
        {"id": "instagram_download", "name": "Instagram downloads", "status": "connected" if li else "not_configured",
         "detail": f"Last loaded {li['at'][:10]}: {li.get('following') or 0:,} following, {li.get('conversations', 0)} chats." if li
                   else "Request your data from Instagram, then Load it on the Dashboard.", "stage": 0},
        {"id": "instagram_browser", "name": "Instagram (Convo's browser)", "status":
            "connected" if check.startswith("Self-check: 6 of 6") else "error" if check else "unknown",
         "detail": check.splitlines()[0] if check else "Run the self-check in Account Settings to confirm Convo can use Instagram.", "stage": 0},
    ]
    for p, info in PLATFORMS.items():
        items.append({"id": f"{p}_api", "name": f"{info['name']} API", "status": "planned", "stage": info["stage"],
                      "detail": info["official"]["connect"], "can": info["official"]["can"], "cannot": info["official"]["cannot"]})
    return items


# ---------------------------------------------------------------------------------------------- web API
class AccountBody(BaseModel):
    platform: str | None = None
    handle: str | None = None
    display_name: str | None = None
    role: str | None = None
    niche: str | None = None
    purpose: str | None = None
    notes: str | None = None
    group_id: int | None = None
    referral_link: str | None = None


class GroupBody(BaseModel):
    name: str | None = None
    color: str | None = None
    description: str | None = None


class ReadyBody(BaseModel):
    kind: str


def _text(v: str | None, n: int) -> str | None:
    v = (v or "").strip()
    return v[:n] or None


def register(api: FastAPI, a, auth) -> None:
    st: Store = a.store
    dep = [Depends(auth)]
    ensure_main_account(st, a.s.account_username)

    @api.get("/api/hub/overview", dependencies=dep)
    def overview():
        c = lambda sql, *p: st.scalar(sql, p) or 0
        main = st.one("SELECT * FROM accounts WHERE role = 'main' ORDER BY id LIMIT 1")
        accounts = [account_row(st, r) for r in st.all("SELECT * FROM accounts ORDER BY role = 'main' DESC, id")]
        main_row = next((x for x in accounts if main and x["id"] == main["id"]), None)
        li = st.get("last_import")
        followers_series = st.all("SELECT at, followers, following FROM snapshots WHERE account_id = ? ORDER BY at",
                                  (main["id"],)) if main else []
        return {
            "me": {"username": a.s.account_username, "name": (li or {}).get("your_name")},
            "practice": a.practice is not None,
            "accounts": accounts,
            "kpis": {
                "followers": {"value": main_row["followers"] if main_row else None, "as_of": main_row["as_of"] if main_row else None,
                              "source": "Instagram download" if main_row and main_row["followers"] is not None else None,
                              "change_7d": main_row["followers_change_7d"] if main_row else None},
                "following": main_row["following"] if main_row else None,
                "conversations": c("SELECT COUNT(*) FROM threads WHERE is_group = 0"),
                "waiting_on_you": c("SELECT COUNT(*) FROM threads WHERE is_group = 0 AND is_request = 0 AND last_from_me = 0"),
                "sent_7d": c("SELECT COUNT(*) FROM drafts WHERE status = 'sent' AND sent_at >= ?", iso_ago(days=7)),
                "received_7d": c("SELECT COUNT(*) FROM messages m JOIN threads t ON t.id = m.thread_id WHERE m.from_me = 0 AND t.is_group = 0 "
                                 "AND m.at >= ?", int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)),
                "views": None, "reach": None, "engagement": None,
                "unavailable": "Views, reach and engagement need the platform connections (stages 4 and 5).",
            },
            "followers_series": followers_series,
            "activity_series": activity_series(st, 30),
            "hello": hello_stats(st),
            "replies": {"to_check": c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'draft'"),
                        "writing": c("SELECT COUNT(*) FROM drafts WHERE status = 'writing'"),
                        "approved": c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'approved'")},
            "platforms": [{"id": p, "name": info["name"], "color": info["color"],
                           "accounts": c("SELECT COUNT(*) FROM accounts WHERE platform = ?", p),
                           "connected": bool(c("SELECT COUNT(*) FROM accounts WHERE platform = ? AND status = 'connected'", p)),
                           "followers": next((x["followers"] for x in accounts if x["platform"] == p and x["role"] == "main"), None),
                           "stage": info["stage"]} for p, info in PLATFORMS.items()],
            "sender": {"active": a.sender.active, "status": a.sender.status, "stopped": a.sender.halt.is_set()},
            "next_steps": next_steps(st, a),
            "recent": st.all("SELECT id, at, kind, text FROM activity ORDER BY id DESC LIMIT 8"),
            "last_import": li,
        }

    @api.get("/api/hub/conversations", dependencies=dep)
    def conversations(q: str = "", show: str = "all", offset: int = 0, limit: int = 40):
        where = {"waiting": "t.last_from_me = 0 AND t.is_request = 0", "requests": "t.is_request = 1",
                 "groups": "t.is_group = 1"}.get(show, "1")
        like = f"%{q.strip().lstrip('@').lower()}%"
        base = f"""FROM threads t LEFT JOIN people p ON p.username = t.username
            WHERE {where} AND (LOWER(COALESCE(t.other_name, '')) LIKE ? OR LOWER(COALESCE(t.username, '')) LIKE ?
                               OR LOWER(COALESCE(t.title, '')) LIKE ?)"""
        total = st.scalar("SELECT COUNT(*) " + base, (like, like, like))
        rows = st.all(f"""SELECT t.*, p.display_name, p.interest, p.concern, p.summary,
            (SELECT text FROM messages m WHERE m.thread_id = t.id ORDER BY at DESC LIMIT 1) AS last_text,
            (SELECT status FROM drafts d WHERE d.thread_id = t.id ORDER BY d.id DESC LIMIT 1) AS draft_status
            {base} ORDER BY t.last_at DESC LIMIT ? OFFSET ?""", (like, like, like, min(limit, 100), max(offset, 0)))
        return {"total": total, "items": rows}

    @api.get("/api/hub/conversations/{thread_id}", dependencies=dep)
    def conversation(thread_id: str):
        t = st.one("SELECT * FROM threads WHERE id = ?", (thread_id,))
        if not t:
            raise HTTPException(404, "No such conversation")
        return {"thread": t,
                "messages": st.all("SELECT from_me, sender, text, at FROM messages WHERE thread_id = ? ORDER BY at", (thread_id,)),
                "drafts": st.all("SELECT id, kind, text, status, note, sent_at, updated_at, context FROM drafts WHERE thread_id = ? "
                                 "ORDER BY id DESC", (thread_id,)),
                "person": st.one("SELECT username, display_name, summary, interest, interest_why, concern, concern_why, note, memory "
                                 "FROM people WHERE username = ?", (t["username"],)) if t["username"] else None}

    @api.get("/api/hub/activity", dependencies=dep)
    def activity(kind: str = "", before: int = 0, limit: int = 50):
        kinds = [k for k in kind.split(",") if k in ("good", "info", "warn", "problem")]
        where = (f"kind IN ({','.join('?' * len(kinds))})" if kinds else "1") + (" AND id < ?" if before else "")
        params = (*kinds, *([before] if before else []), min(limit, 200))
        return {"items": st.all(f"SELECT id, at, kind, text FROM activity WHERE {where} ORDER BY id DESC LIMIT ?", params)}

    @api.get("/api/hub/hello", dependencies=dep)
    def hello():
        return {**hello_stats(st), "series": activity_series(st, 30), "sender": {"active": a.sender.active, "status": a.sender.status,
                "stopped": a.sender.halt.is_set()}, "daily_messages": a.s.pace.daily_messages, "send_at": a.s.send_at}

    # ------------------------------------------------------------------ reviewing hellos and replies
    READY = ("status = 'draft' AND text != '' AND NOT COALESCE(json_extract(context, '$.needs_look'), 0) "
             "AND NOT COALESCE(json_extract(context, '$.manual'), 0)")
    LOOK = ("(COALESCE(json_extract(context, '$.needs_look'), 0) OR COALESCE(json_extract(context, '$.manual'), 0) "
            "OR note IS NOT NULL)")

    @api.get("/api/hub/drafts/summary", dependencies=dep)
    def drafts_summary(kind: str = "hello"):
        c = lambda where: st.scalar(f"SELECT COUNT(*) FROM drafts WHERE kind = ? AND {where}", (kind,)) or 0
        return {"waiting": c("status = 'draft'"), "ready": c(READY), "look": c(f"status = 'draft' AND {LOOK}"),
                "interesting": c("status = 'draft' AND COALESCE(json_extract(context, '$.interest'), 0) >= 2"),
                "approved": c("status = 'approved'"), "other": c("status IN ('unsure', 'failed', 'skipped')"),
                "sent": c("status = 'sent'"), "daily_messages": a.s.pace.daily_messages}

    @api.post("/api/hub/drafts/approve-ready", dependencies=dep)
    def approve_ready_ids(body: ReadyBody):
        """Approve every ready one (as "Approve all ready"), returning their ids so the screen can offer Undo."""
        if body.kind not in ("hello", "reply"):
            raise HTTPException(404, "unknown")
        ids = [r["id"] for r in st.all(f"SELECT id FROM drafts WHERE kind = ? AND {READY} ORDER BY id", (body.kind,))]
        done = 0
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            done += st.execute(f"UPDATE drafts SET status = 'approved', updated_at = ? WHERE id IN ({','.join('?' * len(chunk))}) "
                               f"AND {READY}", (now_iso(), *chunk)).rowcount
        if done:
            st.log("info", f"Approved {done} {'hellos' if body.kind == 'hello' else 'replies'}.")
        return {"approved": done, "ids": ids}

    @api.get("/api/hub/integrations", dependencies=dep)
    def integrations_status():
        return {"items": integrations(st, a)}

    # ---- accounts and groups
    @api.get("/api/hub/accounts", dependencies=dep)
    def accounts(role: str = ""):
        where, params = ("role = ?", (role,)) if role in ROLES else ("1", ())
        return {"items": [account_row(st, r) for r in st.all(f"SELECT * FROM accounts WHERE {where} ORDER BY role = 'main' DESC, id", params)],
                "platforms": [{"id": p, "name": i["name"], "color": i["color"]} for p, i in PLATFORMS.items()],
                "max_launch": MAX_LAUNCH_ACCOUNTS}

    def _group_ok(gid):
        if gid is not None and not st.scalar("SELECT 1 FROM account_groups WHERE id = ?", (gid,)):
            raise HTTPException(400, "That group doesn't exist")

    @api.post("/api/hub/accounts", dependencies=dep)
    def add_account(body: AccountBody):
        role = body.role or "launch"
        if role not in ("launch", "other"):
            raise HTTPException(400, "New accounts are launch or other accounts")
        try:
            handle = clean_handle(body.platform or "", body.handle or "")
        except ValueError as e:
            raise HTTPException(400, str(e).capitalize())
        if role == "launch" and (st.scalar("SELECT COUNT(*) FROM accounts WHERE role = 'launch'") or 0) >= MAX_LAUNCH_ACCOUNTS:
            raise HTTPException(400, f"The launch network holds up to {MAX_LAUNCH_ACCOUNTS} accounts")
        if st.scalar("SELECT 1 FROM accounts WHERE platform = ? AND handle = ?", (body.platform, handle)):
            raise HTTPException(409, "That account is already here")
        _group_ok(body.group_id)
        link = _text(body.referral_link, 300)
        if link and not re.match(r"^https?://", link):
            raise HTTPException(400, "The tracking link should start with https://")
        st.execute("INSERT INTO accounts(platform, handle, display_name, role, niche, purpose, notes, group_id, referral_link, "
                   "connection, status, status_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', 'not_connected', ?, ?, ?)",
                   (body.platform, handle, _text(body.display_name, 80), role, _text(body.niche, 80), _text(body.purpose, 300),
                    _text(body.notes, 1000), body.group_id, link,
                    f"Live numbers arrive when {PLATFORMS[body.platform]['name']} is connected (stage {PLATFORMS[body.platform]['stage']})",
                    now_iso(), now_iso()))
        aid = st.scalar("SELECT id FROM accounts WHERE platform = ? AND handle = ?", (body.platform, handle))
        st.log("info", f"Added {PLATFORMS[body.platform]['name']} account @{handle} to the {'launch network' if role == 'launch' else 'accounts'}.")
        return account_row(st, st.one("SELECT * FROM accounts WHERE id = ?", (aid,)))

    @api.patch("/api/hub/accounts/{aid}", dependencies=dep)
    def edit_account(aid: int, body: AccountBody):
        r = st.one("SELECT * FROM accounts WHERE id = ?", (aid,))
        if not r:
            raise HTTPException(404, "No such account")
        fields = {}
        for f, n in (("display_name", 80), ("niche", 80), ("purpose", 300), ("notes", 1000)):
            v = getattr(body, f)
            if v is not None:
                fields[f] = _text(v, n)
        if body.referral_link is not None:
            link = _text(body.referral_link, 300)
            if link and not re.match(r"^https?://", link):
                raise HTTPException(400, "The tracking link should start with https://")
            fields["referral_link"] = link
        if "group_id" in body.model_fields_set:
            _group_ok(body.group_id)
            fields["group_id"] = body.group_id
        if body.role is not None and body.role != r["role"]:
            if r["role"] == "main" or body.role not in ("launch", "other"):
                raise HTTPException(400, "Your main account stays your main account")
            if body.role == "launch" and (st.scalar("SELECT COUNT(*) FROM accounts WHERE role = 'launch'") or 0) >= MAX_LAUNCH_ACCOUNTS:
                raise HTTPException(400, f"The launch network holds up to {MAX_LAUNCH_ACCOUNTS} accounts")
            fields["role"] = body.role
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            st.execute(f"UPDATE accounts SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), now_iso(), aid))
        return account_row(st, st.one("SELECT * FROM accounts WHERE id = ?", (aid,)))

    @api.delete("/api/hub/accounts/{aid}", dependencies=dep)
    def remove_account(aid: int):
        r = st.one("SELECT * FROM accounts WHERE id = ?", (aid,))
        if not r:
            raise HTTPException(404, "No such account")
        if r["role"] == "main":
            raise HTTPException(400, "Your main account can't be removed")
        st.backup(f"before-removing-{r['handle']}")
        st.execute("DELETE FROM snapshots WHERE account_id = ?", (aid,))
        st.execute("DELETE FROM accounts WHERE id = ?", (aid,))
        st.log("info", f"Removed @{r['handle']} from Convo (nothing changed on {PLATFORMS.get(r['platform'], {}).get('name', 'the platform')}).")
        return {"ok": True}

    @api.get("/api/hub/groups", dependencies=dep)
    def groups():
        return {"items": st.all("SELECT g.*, (SELECT COUNT(*) FROM accounts a WHERE a.group_id = g.id) AS accounts "
                                "FROM account_groups g ORDER BY g.name")}

    @api.post("/api/hub/groups", dependencies=dep)
    def add_group(body: GroupBody):
        name = _text(body.name, 60)
        if not name:
            raise HTTPException(400, "Give the group a name")
        if st.scalar("SELECT 1 FROM account_groups WHERE LOWER(name) = LOWER(?)", (name,)):
            raise HTTPException(409, "There's already a group with that name")
        color = body.color if body.color and re.fullmatch(r"#[0-9a-fA-F]{6}", body.color) else "#8b5cf6"
        st.execute("INSERT INTO account_groups(name, color, description, created_at) VALUES (?, ?, ?, ?)",
                   (name, color, _text(body.description, 300), now_iso()))
        return st.one("SELECT * FROM account_groups WHERE name = ?", (name,))

    @api.patch("/api/hub/groups/{gid}", dependencies=dep)
    def edit_group(gid: int, body: GroupBody):
        if not st.one("SELECT 1 FROM account_groups WHERE id = ?", (gid,)):
            raise HTTPException(404, "No such group")
        if body.name is not None:
            name = _text(body.name, 60)
            if not name:
                raise HTTPException(400, "Give the group a name")
            if st.scalar("SELECT 1 FROM account_groups WHERE LOWER(name) = LOWER(?) AND id != ?", (name, gid)):
                raise HTTPException(409, "There's already a group with that name")
            st.execute("UPDATE account_groups SET name = ? WHERE id = ?", (name, gid))
        if body.color is not None and re.fullmatch(r"#[0-9a-fA-F]{6}", body.color):
            st.execute("UPDATE account_groups SET color = ? WHERE id = ?", (body.color, gid))
        if body.description is not None:
            st.execute("UPDATE account_groups SET description = ? WHERE id = ?", (_text(body.description, 300), gid))
        return st.one("SELECT * FROM account_groups WHERE id = ?", (gid,))

    @api.delete("/api/hub/groups/{gid}", dependencies=dep)
    def remove_group(gid: int):
        st.execute("UPDATE accounts SET group_id = NULL WHERE group_id = ?", (gid,))
        st.execute("DELETE FROM account_groups WHERE id = ?", (gid,))
        return {"ok": True}

    @api.get("/api/hub/analytics", dependencies=dep)
    def analytics_view(days: int = 30):
        return analytics(st, max(7, min(days, 365)))

    @api.get("/api/hub/automations", dependencies=dep)
    def automations_view():
        return {"items": automations(st, a)}

    # ---- backups
    @api.get("/api/hub/backups", dependencies=dep)
    def backups():
        return {"items": st.backups()}

    @api.post("/api/hub/backups", dependencies=dep)
    def backup_now():
        p = st.backup("manual")
        if p is None:
            raise HTTPException(400, "This copy of Convo keeps nothing on disk")
        st.log("good", f"Backed up your data ({p.name}).")
        return {"name": p.name}


# ---------------------------------------------------------------------------------------------- analytics and automations
AUTOMATIONS_LIVE = [
    # id, name, what it does, needs approval?, settings key, how to find its last run in the activity log
    ("overnight", "Write replies overnight", "After each download, replies are written through Anthropic's batch service at half price.",
     False, "write_overnight", ("Wrote ", "Sent ", "chats to be written overnight")),
    ("send_at", "Send at a set time", "Each day at your time, sends what you've approved, within your daily number.",
     True, "send_at", ("started sending",)),
    ("quiet", "Quiet hours", "Nothing is sent between these times.", False, "quiet_from", ("Quiet hours",)),
    ("budget", "Daily AI spending limit", "Writing and sending pause for the day once the AI has cost this much.",
     False, "daily_ai_budget_usd", ("spending limit",)),
    ("safety", "Instagram safety stops", "Stops for 6 hours if Instagram shows any 'try again later' warning, and after three problems in a row.",
     False, None, ("Instagram asked", "stopped", "Stopped")),
    ("follow_up", "Follow-up reminders", "Lists who's waiting on you and hellos with no answer, and tells you on the day of a reminder you set.",
     False, None, ("Reminder set",)),
    ("content_reminders", "Posting reminders", "Tells you when a scheduled post's time comes, with the words ready to paste.",
     False, None, ("Time to post",)),
    ("caption", "Caption writing", "Writes captions, titles and hashtags for each platform from your notes, when you press Write with AI.",
     True, None, ()),
]
AUTOMATIONS_PLANNED = [
    ("repurpose", "Content repurposing", "Suggests older content worth reposting in a new format.", True, 6),
    ("analytics", "Analytics updates", "Pulls followers, views and engagement from connected platforms every few hours.", False, 4),
    ("publishing", "Scheduled publishing", "Publishes approved posts at their time through the official APIs.", True, 4),
    ("comments", "Comment notifications", "Tells you about new comments worth answering.", False, 5),
    ("comment_replies", "AI-assisted comment replies", "Writes replies to comments for you to approve.", True, 5),
    ("weekly_report", "Weekly performance report", "A plain summary of the week, every Monday.", False, 6),
    ("insights", "Audience insights", "What your audience responds to, from your analytics.", False, 6),
    ("missions", "Weekly mission tracking", "Updates mission progress from real activity.", False, 6),
    ("growth", "Growth recommendations", "Suggestions based on what's actually working.", False, 6),
    ("launch_report", "Launch network reporting", "Weekly results for each supporting account.", False, 6),
]


def automations(store: Store, a) -> list[dict]:
    s = a.s
    out = []
    for aid, name, what, approval, key, marks in AUTOMATIONS_LIVE:
        last = None
        for m in marks:
            r = store.one("SELECT at, text FROM activity WHERE text LIKE ? ORDER BY id DESC LIMIT 1", (f"%{m}%",))
            if r and (last is None or r["at"] > last["at"]):
                last = r
        on = {"overnight": s.write_overnight, "send_at": bool(s.send_at), "quiet": bool(s.quiet_from and s.quiet_to),
              "budget": True, "safety": True, "follow_up": True, "content_reminders": True, "caption": True}[aid]
        nxt = None
        if aid == "send_at" and s.send_at:
            now = datetime.now()
            hh, mm = map(int, s.send_at.split(":"))
            t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if t <= now or store.get("scheduled_run") == now.strftime("%Y-%m-%d"):
                t += timedelta(days=1)
            nxt = t.astimezone().isoformat(timespec="minutes")
        out.append({"id": aid, "name": name, "description": what, "needs_approval": approval, "status": "live", "enabled": on,
                    "always_on": aid in ("budget", "safety", "follow_up", "content_reminders", "caption"), "last_run": last, "next_run": nxt,
                    "config": {"send_at": s.send_at, "quiet_from": s.quiet_from, "quiet_to": s.quiet_to,
                               "daily_ai_budget_usd": s.daily_ai_budget_usd}})
    for aid, name, what, approval, stage in AUTOMATIONS_PLANNED:
        out.append({"id": aid, "name": name, "description": what, "needs_approval": approval, "status": "planned", "stage": stage,
                    "enabled": False, "last_run": None, "next_run": None})
    return out


def analytics(store: Store, days: int) -> dict:
    c = lambda sql, *p: store.scalar(sql, p) or 0
    interest = {str(k): c("SELECT COUNT(*) FROM people WHERE interest = ?", k) for k in (0, 1, 2, 3)}
    top = store.all("""SELECT t.username, t.other_name, COUNT(*) AS messages,
        SUM(CASE WHEN m.from_me = 0 THEN 1 ELSE 0 END) AS theirs FROM messages m JOIN threads t ON t.id = m.thread_id
        WHERE t.is_group = 0 AND m.at >= ? GROUP BY t.id ORDER BY messages DESC LIMIT 8""",
                    (int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000),))
    return {
        "days": days, "series": activity_series(store, days),
        "relationships": {"following": c("SELECT COUNT(*) FROM people WHERE i_follow = 1"),
                          "followers": c("SELECT COUNT(*) FROM people WHERE follows_me = 1"),
                          "mutual": c("SELECT COUNT(*) FROM people WHERE i_follow = 1 AND follows_me = 1"),
                          "chatted": c("SELECT COUNT(DISTINCT username) FROM threads WHERE username IS NOT NULL AND is_group = 0")},
        "interest": interest, "top_conversations": top, "hello": hello_stats(store),
        "followers": store.all("""SELECT s.at, s.followers, s.following, a.handle FROM snapshots s JOIN accounts a ON a.id = s.account_id
            WHERE a.role = 'main' ORDER BY s.at"""),
        "availability": [
            {"metric": m, **{p: v for p, v in zip(("instagram", "tiktok", "youtube"), row)}} for m, *row in [
                ("Followers", "API (Creator account)", "API", "API"), ("Views", "API", "API", "API"), ("Reach", "API", "Not available", "Not available"),
                ("Impressions", "Being replaced by views", "Not available", "API"), ("Likes, comments, shares", "API", "API", "API"),
                ("Saves", "API", "Not available", "Not available"), ("Watch time and retention", "Reels only, partly", "Not available", "API"),
                ("Profile visits", "API", "Not available", "Not available"), ("Link clicks", "API", "Not available", "Not available"),
                ("Traffic sources", "Not available", "Not available", "API")]],
    }

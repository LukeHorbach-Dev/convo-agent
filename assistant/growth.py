"""Growth: weekly missions with XP and streaks, longer goals, the Organic Independence Score, launch mode for the
launch network, and collaborations. Everything is counted from what really happened (your downloads, your content,
connected platforms, numbers you typed in); nothing is estimated.

Missions reward genuine activity: real two-way conversations, answering people, posting and planning. There are no
"send N messages" missions: a numbers game with DMs is what gets accounts restricted.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .store import Store, now_iso

# ---------------------------------------------------------------------------------------------- what can be counted
METRICS = {
    "conversations": "Real conversations (you and them both wrote)",
    "replies_sent": "Replies sent",
    "waiting": "People waiting on you (fewer is better)",
    "posts_published": "Posts published",
    "posts_planned": "Posts scheduled for the next 7 days",
    "ideas_saved": "New post ideas saved",
    "clicks_checked_in": "Replies to people you'd click with",
    "hellos_reviewed": "Hellos you reviewed (approved or skipped)",
}
GOAL_METRICS = {"followers": "Followers on an account", "youtube_views_28d": "YouTube views (last 28 days)",
                "posts_per_week": "Posts a week (last 4 weeks)", "conversations_per_week": "Real conversations a week (last 4 weeks)"}
BUILT_IN = [  # key, title, metric, target, xp
    ("conversations", "Have 5 real conversations", "conversations", 5, 50),
    ("waiting", "Reply to everyone waiting on you", "waiting", 0, 30),
    ("post", "Post 3 times", "posts_published", 3, 50),
    ("plan", "Have 3 posts planned for next week", "posts_planned", 3, 30),
    ("ideas", "Save 3 new post ideas", "ideas_saved", 3, 20),
    ("click", "Check in with 2 people you'd click with", "clicks_checked_in", 2, 30),
]
XP_PER_LEVEL = 250
STREAK_MISSIONS = 3            # a week counts towards the streak with this many missions done


def monday(d: date | None = None) -> date:
    d = d or datetime.now().date()
    return d - timedelta(days=d.weekday())


def _ms(d: datetime) -> int:
    return int(d.timestamp() * 1000)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def measure(st: Store, metric: str, start: datetime, end: datetime) -> int:
    """One metric between start and end (local times). Point-in-time ones ("waiting", "posts_planned") use now."""
    c = lambda sql, *p: st.scalar(sql, p) or 0
    if metric == "conversations":
        return c("""SELECT COUNT(*) FROM threads t WHERE t.is_group = 0
            AND EXISTS (SELECT 1 FROM messages m WHERE m.thread_id = t.id AND m.from_me = 1 AND m.at >= ? AND m.at < ?)
            AND EXISTS (SELECT 1 FROM messages m WHERE m.thread_id = t.id AND m.from_me = 0 AND m.at >= ? AND m.at < ?)""",
                 _ms(start), _ms(end), _ms(start), _ms(end))
    if metric == "replies_sent":
        return c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'sent' AND sent_at >= ? AND sent_at < ?", _iso(start), _iso(end))
    if metric == "waiting":
        from .followups import followups
        return followups(st)["counts"]["waiting"]
    if metric == "posts_published":
        return c("SELECT COUNT(*) FROM content WHERE status = 'posted' AND posted_at >= ? AND posted_at < ?", _iso(start), _iso(end))
    if metric == "posts_planned":
        now = datetime.now(timezone.utc)
        return c("SELECT COUNT(*) FROM content WHERE status = 'scheduled' AND scheduled_at >= ? AND scheduled_at < ?", _iso(now), _iso(now + timedelta(days=7)))
    if metric == "ideas_saved":
        return c("SELECT COUNT(*) FROM content WHERE created_at >= ? AND created_at < ?", _iso(start), _iso(end))
    if metric == "clicks_checked_in":
        return c("""SELECT COUNT(DISTINCT d.username) FROM drafts d JOIN people p ON p.username = d.username
            WHERE d.kind = 'reply' AND d.status = 'sent' AND p.interest >= 3 AND d.sent_at >= ? AND d.sent_at < ?""", _iso(start), _iso(end))
    if metric == "hellos_reviewed":
        return c("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status IN ('approved', 'skipped', 'sent') AND updated_at >= ? AND updated_at < ?",
                 _iso(start), _iso(end))
    raise ValueError(metric)


def missions(st: Store, week: date | None = None) -> dict:
    """This week's missions, saved into the history as they go."""
    wk = week or monday()
    start = datetime.combine(wk, datetime.min.time()).astimezone()
    end = start + timedelta(days=7)
    rows = []
    defs = [(k, t, m, tg, xp) for k, t, m, tg, xp in BUILT_IN] + \
           [(f"custom:{r['id']}", r["title"], r["metric"], r["target"], r["xp"]) for r in st.all("SELECT * FROM missions WHERE active = 1")]
    for key, title, metric, target, xp in defs:
        value = measure(st, metric, start, end)
        done = value <= target if metric == "waiting" else value >= target
        rows.append({"key": key, "title": title, "metric": metric, "target": target, "value": value, "done": done, "xp": xp,
                     "custom": key.startswith("custom:"), "progress": 1.0 if done else (0.0 if metric == "waiting" else min(1.0, value / max(target, 1)))})
        st.execute("INSERT OR REPLACE INTO mission_weeks(week, mission, title, target, value, done, xp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (wk.isoformat(), key, title, target, value, int(done), xp if done else 0))
    hist = st.all("SELECT week, SUM(done) AS done, SUM(xp) AS xp, COUNT(*) AS n FROM mission_weeks GROUP BY week ORDER BY week DESC")
    total_xp = sum(h["xp"] or 0 for h in hist)
    streak = 0
    for i, h in enumerate(hist):
        if h["done"] >= STREAK_MISSIONS:
            streak += 1
        elif i == 0 and h["week"] == wk.isoformat():
            continue                                   # this week isn't over yet
        else:
            break
    badges = _badges(st, hist, streak)
    return {"week": wk.isoformat(), "missions": rows, "done": sum(r["done"] for r in rows), "xp_week": sum(r["xp"] for r in rows if r["done"]),
            "xp_total": total_xp, "level": total_xp // XP_PER_LEVEL + 1, "level_progress": (total_xp % XP_PER_LEVEL) / XP_PER_LEVEL,
            "streak": streak, "history": hist[:12], "badges": badges, "metrics": METRICS}


def _badges(st: Store, hist: list[dict], streak: int) -> list[dict]:
    c = lambda sql, *p: st.scalar(sql, p) or 0
    posted = c("SELECT COUNT(*) FROM content WHERE status = 'posted'")
    weeks_all = sum(1 for h in hist if h["done"] == h["n"] and h["n"] > 0)
    convos = c("SELECT COUNT(*) FROM threads t WHERE is_group = 0 AND EXISTS (SELECT 1 FROM messages m WHERE m.thread_id = t.id AND m.from_me = 1) "
               "AND EXISTS (SELECT 1 FROM messages m WHERE m.thread_id = t.id AND m.from_me = 0)")
    goals = c("SELECT COUNT(*) FROM goals WHERE done_at IS NOT NULL")
    return [
        {"id": "first_post", "name": "First post", "how": "Mark a post as posted", "earned": posted >= 1},
        {"id": "ten_posts", "name": "Ten posts", "how": "Post 10 times", "earned": posted >= 10},
        {"id": "streak4", "name": "Four-week streak", "how": f"{STREAK_MISSIONS}+ missions a week, four weeks running", "earned": streak >= 4},
        {"id": "clean_sweep", "name": "Clean sweep", "how": "Finish every mission in a week", "earned": weeks_all >= 1},
        {"id": "hundred", "name": "100 real conversations", "how": "Chats where you both wrote", "earned": convos >= 100},
        {"id": "goal", "name": "Goal reached", "how": "Reach one of your growth goals", "earned": goals >= 1},
    ]


# ---------------------------------------------------------------------------------------------- goals
def goal_value(st: Store, metric: str, account_id: int | None) -> float | None:
    now = datetime.now().astimezone()
    if metric == "followers":
        r = st.one("SELECT followers FROM snapshots WHERE account_id = ? AND followers IS NOT NULL ORDER BY at DESC LIMIT 1", (account_id,))
        return r and r["followers"]
    if metric == "youtube_views_28d":
        rows = st.all("SELECT metrics FROM platform_daily WHERE platform = 'youtube' ORDER BY date DESC LIMIT 28")
        return sum(json.loads(r["metrics"]).get("views", 0) for r in rows) if rows else None
    if metric == "posts_per_week":
        return round(measure(st, "posts_published", now - timedelta(days=28), now) / 4, 1)
    if metric == "conversations_per_week":
        return round(measure(st, "conversations", now - timedelta(days=28), now) / 4, 1)
    raise ValueError(metric)


def goal_row(st: Store, g: dict) -> dict:
    cur = goal_value(st, g["metric"], g["account_id"])
    start = g["start_value"]
    out = {**g, "current": cur, "status": "no_data", "why": "No number for this yet."}
    if cur is None:
        return out
    if cur >= g["target"]:
        if not g["done_at"]:
            st.execute("UPDATE goals SET done_at = ? WHERE id = ?", (now_iso(), g["id"]))
            out["done_at"] = now_iso()
        return {**out, "status": "reached", "why": "You reached it.", "progress": 1.0}
    span = (g["target"] - (start or 0)) or 1
    out["progress"] = max(0.0, min(1.0, (cur - (start or 0)) / span)) if start is not None else None
    if not g["due"] or start is None:
        return {**out, "status": "going", "why": "No date set, so it's not judged on pace."}
    began = datetime.fromisoformat(g["start_at"])
    due = datetime.fromisoformat(g["due"] if "T" in g["due"] else g["due"] + "T23:59:59").astimezone()
    now = datetime.now().astimezone()
    elapsed, total = (now - began).total_seconds(), (due - began).total_seconds()
    if elapsed < 3 * 86400 or total <= 0:
        return {**out, "status": "too_early", "why": "Too early to tell (give it a few days)."}
    if now > due:
        return {**out, "status": "missed", "why": f"The date passed at {cur:g} of {g['target']:g}."}
    expected = (start or 0) + span * (elapsed / total)
    ahead = cur >= expected
    return {**out, "status": "on_track" if ahead else "behind",
            "why": f"At this pace you'd {'reach' if ahead else 'not quite reach'} it by the date (now {cur:g}, the pace line is at {expected:.0f})."}


# ---------------------------------------------------------------------------------------------- independence
ORGANIC = {"YT_SEARCH", "RELATED_VIDEO", "BROWSE", "SHORTS", "HASHTAGS", "SOUND_PAGE", "YT_OTHER_PAGE", "NOTIFICATION", "SUBSCRIBER", "PLAYLIST"}


def independence(st: Store) -> dict:
    """How self-sustaining the brand is, from the parts that can really be measured. Each part 0-100; the score is
    their average and is only given when at least two parts could be measured."""
    parts = []
    tr = (st.get("youtube_traffic") or {}).get("sources") or []
    total = sum(s["views"] for s in tr)
    if total >= 50:
        organic = sum(s["views"] for s in tr if s["source"] in ORGANIC)
        parts.append({"id": "discovery", "name": "Found by the platform", "score": round(100 * organic / total), "source": "YouTube traffic sources, 28 days",
                      "detail": f"{organic:,} of {total:,} views came from search, suggestions, browse, Shorts or subscribers."})
    else:
        parts.append({"id": "discovery", "name": "Found by the platform", "score": None, "source": "YouTube traffic sources",
                      "detail": "Needs YouTube connected with at least 50 views in 28 days."})
    now = datetime.now().astimezone()
    active = st.scalar("SELECT COUNT(DISTINCT thread_id) FROM messages m JOIN threads t ON t.id = m.thread_id WHERE t.is_group = 0 AND m.from_me = 0 AND m.at >= ?",
                       (_ms(now - timedelta(days=30)),)) or 0
    real = measure(st, "conversations", now - timedelta(days=30), now)
    if active >= 5:
        parts.append({"id": "conversations", "name": "Real conversations", "score": round(100 * min(real, active) / active), "source": "Your Instagram downloads, 30 days",
                      "detail": f"{real} of the {active} people who wrote to you had a real back-and-forth with you."})
    else:
        parts.append({"id": "conversations", "name": "Real conversations", "score": None, "source": "Your Instagram downloads",
                      "detail": "Needs at least 5 people writing to you in 30 days (load a recent download)."})
    posts = st.all("SELECT views, comments FROM platform_posts WHERE views IS NOT NULL AND views > 0 ORDER BY published_at DESC LIMIT 30")
    views = sum(p["views"] for p in posts)
    if len(posts) >= 3 and views >= 300:
        per_k = 1000 * sum(p["comments"] or 0 for p in posts) / views
        parts.append({"id": "engagement", "name": "Genuine comments", "score": round(min(100, per_k * 10)), "source": "Connected platforms, latest posts",
                      "detail": f"{per_k:.1f} comments for every 1,000 views on your latest {len(posts)} posts (10 or more scores full marks)."})
    else:
        parts.append({"id": "engagement", "name": "Genuine comments", "score": None, "source": "Connected platforms",
                      "detail": "Needs a connected platform with 3+ posts and 300+ views."})
    weeks = [monday(now.date()) - timedelta(weeks=i) for i in range(1, 9)]
    if st.scalar("SELECT COUNT(*) FROM content WHERE status = 'posted'"):
        posted_weeks = sum(1 for w in weeks if measure(st, "posts_published", datetime.combine(w, datetime.min.time()).astimezone(),
                                                       datetime.combine(w + timedelta(days=7), datetime.min.time()).astimezone()))
        parts.append({"id": "consistency", "name": "Posting consistently", "score": round(100 * posted_weeks / 8), "source": "Your Content Library, 8 weeks",
                      "detail": f"You posted in {posted_weeks} of the last 8 weeks."})
    else:
        parts.append({"id": "consistency", "name": "Posting consistently", "score": None, "source": "Your Content Library",
                      "detail": "Mark posts as posted (or publish from the Studio) to count this."})
    have = [p["score"] for p in parts if p["score"] is not None]
    score = round(sum(have) / len(have)) if len(have) >= 2 else None
    if score is None:
        advice = "Not enough measured yet to give a score. Each part below says what it needs."
    elif score < 35:
        advice = "Suggestion: keep using the launch network to seed reach while your main brand finds its feet."
    elif score < 65:
        advice = "Suggestion: start easing off the launch network: post on the main account first and cross-promote less often."
    else:
        advice = "Suggestion: your main brand is standing on its own. Use the launch network for experiments rather than lifting the main account."
    return {"score": score, "measured": len(have), "parts": parts, "advice": advice}


# ---------------------------------------------------------------------------------------------- launch mode
def launch(st: Store) -> list[dict]:
    from .hub import account_row
    main = st.one("SELECT * FROM accounts WHERE role = 'main' ORDER BY id LIMIT 1")
    out = []
    for r in st.all("SELECT * FROM accounts WHERE role = 'launch' ORDER BY id"):
        a = account_row(st, r)
        posts = st.scalar("SELECT COUNT(*) FROM content WHERE account_id = ? AND status IN ('scheduled', 'due', 'posted')", (r["id"],)) or 0
        promo = st.scalar("SELECT COUNT(*) FROM content WHERE account_id = ? AND (title LIKE ? OR notes LIKE ?)",
                          (main["id"] if main else -1, f"%@{r['handle']}%", f"%@{r['handle']}%")) or 0
        steps = [("niche", "Say what it's about", bool(r["niche"] and r["purpose"])),
                 ("link", "Add its link (to track where followers come from)", bool(r["referral_link"])),
                 ("numbers", "Its numbers: connect it or type them in", a["history_points"] > 0),
                 ("content", "Plan its first post", posts > 0),
                 ("promo", "Plan a shout-out from your main account", promo > 0)]
        out.append({**a, "steps": [{"id": i, "title": t, "done": d} for i, t, d in steps], "done": sum(d for *_, d in steps), "of": len(steps),
                    "posts_planned": posts, "shoutouts": promo})
    return out


# ---------------------------------------------------------------------------------------------- web API
class MissionBody(BaseModel):
    title: str
    metric: str
    target: int
    xp: int = 25


class GoalBody(BaseModel):
    title: str
    metric: str
    target: float
    account_id: int | None = None
    due: str | None = None


class NumbersBody(BaseModel):
    followers: int | None = None
    following: int | None = None
    posts: int | None = None


class CollabBody(BaseModel):
    name: str | None = None
    username: str | None = None
    platform: str | None = None
    stage: str | None = None
    idea: str | None = None
    notes: str | None = None


STAGES = ("idea", "reached_out", "talking", "agreed", "done", "not_now")


def register(api: FastAPI, a, auth) -> None:
    st: Store = a.store
    dep = [Depends(auth)]

    @api.get("/api/hub/missions", dependencies=dep)
    def get_missions():
        return missions(st)

    @api.post("/api/hub/missions", dependencies=dep)
    def add_mission(body: MissionBody):
        if body.metric not in METRICS or body.metric == "waiting":
            raise HTTPException(400, "Pick one of the things Convo can count.")
        if not body.title.strip() or not 1 <= body.target <= 1000:
            raise HTTPException(400, "Give it a name and a target between 1 and 1,000.")
        if body.metric == "hellos_reviewed" and body.target > 200:
            raise HTTPException(400, "Keep hello reviews to 200 or fewer a week.")
        mid = st.execute("INSERT INTO missions(title, metric, target, xp, created_at) VALUES (?, ?, ?, ?, ?)",
                         (body.title.strip()[:80], body.metric, body.target, max(5, min(body.xp, 100)), now_iso())).lastrowid
        return {"id": mid}

    @api.delete("/api/hub/missions/{mid}", dependencies=dep)
    def remove_mission(mid: int):
        st.execute("UPDATE missions SET active = 0 WHERE id = ?", (mid,))
        st.execute("DELETE FROM mission_weeks WHERE mission = ? AND week = ?", (f"custom:{mid}", monday().isoformat()))
        return {"ok": True}

    @api.get("/api/hub/goals", dependencies=dep)
    def get_goals():
        return {"items": [goal_row(st, g) for g in st.all("SELECT * FROM goals ORDER BY done_at IS NOT NULL, COALESCE(due, '9999'), id")],
                "metrics": GOAL_METRICS}

    @api.post("/api/hub/goals", dependencies=dep)
    def add_goal(body: GoalBody):
        if body.metric not in GOAL_METRICS:
            raise HTTPException(400, "Pick one of the things Convo can measure.")
        if body.metric == "followers" and not (body.account_id and st.one("SELECT id FROM accounts WHERE id = ?", (body.account_id,))):
            raise HTTPException(400, "Pick which account.")
        if body.target <= 0:
            raise HTTPException(400, "The target should be more than 0.")
        due = None
        if body.due:
            try:
                due = date.fromisoformat(body.due[:10]).isoformat()
            except ValueError:
                raise HTTPException(400, "That date doesn't look right.")
        start = goal_value(st, body.metric, body.account_id)
        gid = st.execute("INSERT INTO goals(title, metric, account_id, target, start_value, start_at, due, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (body.title.strip()[:100] or GOAL_METRICS[body.metric], body.metric, body.account_id, body.target, start,
                          datetime.now().astimezone().isoformat(timespec="seconds"), due, now_iso())).lastrowid
        return goal_row(st, st.one("SELECT * FROM goals WHERE id = ?", (gid,)))

    @api.delete("/api/hub/goals/{gid}", dependencies=dep)
    def remove_goal(gid: int):
        st.execute("DELETE FROM goals WHERE id = ?", (gid,))
        return {"ok": True}

    @api.get("/api/hub/independence", dependencies=dep)
    def get_independence():
        return independence(st)

    @api.get("/api/hub/launch", dependencies=dep)
    def get_launch():
        return {"items": launch(st)}

    @api.post("/api/hub/accounts/{aid}/numbers", dependencies=dep)
    def type_numbers(aid: int, body: NumbersBody):
        from .hub import record_snapshot
        if not st.one("SELECT id FROM accounts WHERE id = ?", (aid,)):
            raise HTTPException(404, "No such account.")
        if body.followers is None and body.following is None and body.posts is None:
            raise HTTPException(400, "Type at least one number.")
        if any(v is not None and not 0 <= v <= 1_000_000_000 for v in (body.followers, body.following, body.posts)):
            raise HTTPException(400, "Those numbers don't look right.")
        record_snapshot(st, aid, now_iso(), "typed", body.followers, body.following, body.posts)
        return {"ok": True}

    @api.post("/api/hub/launch/{aid}/shoutout", dependencies=dep)
    def plan_shoutout(aid: int):
        acct = st.one("SELECT * FROM accounts WHERE id = ? AND role = 'launch'", (aid,))
        main = st.one("SELECT * FROM accounts WHERE role = 'main' ORDER BY id LIMIT 1")
        if not acct or not main:
            raise HTTPException(404, "No such launch account.")
        cid = st.execute("INSERT INTO content(title, kind, status, notes, platforms, account_id, created_at, updated_at) VALUES (?, 'idea', 'idea', ?, ?, ?, ?, ?)",
                         (f"Shout-out for @{acct['handle']}", f"A post or story on your main account pointing people to @{acct['handle']}"
                          + (f" ({acct['niche']})" if acct["niche"] else "") + (f". Link: {acct['referral_link']}" if acct["referral_link"] else "."),
                          json.dumps([main["platform"]]), main["id"], now_iso(), now_iso())).lastrowid
        return {"content_id": cid}

    @api.get("/api/hub/collabs", dependencies=dep)
    def get_collabs():
        return {"items": st.all("SELECT * FROM collabs ORDER BY stage IN ('done', 'not_now'), updated_at DESC"), "stages": STAGES}

    def collab_fields(body: CollabBody) -> dict:
        f = {k: v.strip()[:2000] for k, v in body.model_dump().items() if isinstance(v, str)}
        if "stage" in f and f["stage"] not in STAGES:
            raise HTTPException(400, "Unknown stage.")
        if "username" in f:
            f["username"] = f["username"].lstrip("@").lower() or None
        return f

    @api.post("/api/hub/collabs", dependencies=dep)
    def add_collab(body: CollabBody):
        f = collab_fields(body)
        if not f.get("name"):
            raise HTTPException(400, "Who is it?")
        f.update(created_at=now_iso(), updated_at=now_iso())
        cid = st.execute(f"INSERT INTO collabs({', '.join(f)}) VALUES ({', '.join('?' * len(f))})", tuple(f.values())).lastrowid
        return st.one("SELECT * FROM collabs WHERE id = ?", (cid,))

    @api.patch("/api/hub/collabs/{cid}", dependencies=dep)
    def edit_collab(cid: int, body: CollabBody):
        f = collab_fields(body)
        if not f:
            raise HTTPException(400, "Nothing to change.")
        f["updated_at"] = now_iso()
        st.execute(f"UPDATE collabs SET {', '.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), cid))
        return st.one("SELECT * FROM collabs WHERE id = ?", (cid,))

    @api.delete("/api/hub/collabs/{cid}", dependencies=dep)
    def remove_collab(cid: int):
        st.execute("DELETE FROM collabs WHERE id = ?", (cid,))
        return {"ok": True}

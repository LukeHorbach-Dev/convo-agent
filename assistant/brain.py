"""The thinking part: picks who gets a hello, and for every chat waiting on Luke writes a reply in his voice,
rates how interesting the person is for him, flags anything worth a closer look, and remembers what they said.

Nothing here touches Instagram. It reads the store (filled from the download) and writes drafts.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from agent.core.errors import AgentError
from agent.core.policy import DISENGAGE, _YOUNG_YEAR, stated_age
from agent.intel.profile import Profile
from agent.intel.reasoning import INJECTION, Drafter, check_text
from agent.intel.voice import in_lukes_voice

from .store import Store, iso_ago, now_iso

THINK_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "Luke's next message, or empty if he shouldn't reply"},
        "needs_human": {"type": "boolean", "description": "true if Luke should look before this goes"},
        "reason": {"type": "string", "description": "why it needs a look, in a few plain words"},
        "summary": {"type": "string", "description": "one short line: who they are and what the chat is about"},
        "interest": {"type": "integer", "enum": [0, 1, 2, 3],
                     "description": "how much Luke would probably enjoy this person: 0 not much, 3 a lot"},
        "interest_why": {"type": "string", "description": "the shared ground, in a few words"},
        "concern": {"type": "string", "enum": ["none", "low", "high"]},
        "concern_why": {"type": "string", "description": "what's concerning, in a few words, or empty"},
        "facts_used": {"type": "array", "items": {"type": "string"}},
        "memory_notes": {"type": "array", "items": {"type": "string"},
                         "description": "short notes worth remembering about them (what they said, open questions)"},
        "language": {"type": "string"},
        "their_message_english": {"type": "string", "description": "their latest message in short, simple English"},
        "reply_english": {"type": "string", "description": "what the reply means, in simple English"},
    },
    "required": ["reply", "needs_human", "reason", "summary", "interest", "interest_why", "concern", "concern_why",
                 "facts_used", "memory_notes", "language", "their_message_english", "reply_english"],
    "additionalProperties": False,
}

TRIAGE_RULES = """
SORTING (fill summary, interest, concern):
interest: 3 when there's real common ground with Luke (shared interests from the facts, someone thoughtful,
curious or creative, someone who could teach him something), 2 friendly and easy, 1 neutral, 0 not much there.
concern: "high" if they might be under 19, ask for money, gift cards, crypto or personal details, push to move
off Instagram fast, send sexual content, threaten, harass, or talk about hurting themselves; "low" for anything
else that deserves a second look (pushy, odd links, a bot or scam feel); otherwise "none". When concern is high,
leave reply empty and set needs_human=true.
memory_notes: only what they actually said about themselves, and their questions Luke hasn't answered."""


class Think(BaseModel):
    reply: str = Field(default="", max_length=600)
    needs_human: bool = False
    reason: str = ""
    summary: str = ""
    interest: int = Field(default=1, ge=0, le=3)
    interest_why: str = ""
    concern: str = "none"
    concern_why: str = ""
    facts_used: list[str] = Field(default_factory=list)
    memory_notes: list[str] = Field(default_factory=list)
    language: str = "English"
    their_message_english: str = ""
    reply_english: str = ""


def load_profile(path: Path) -> Profile:
    return Profile.load(path)


def never_list(path: Path) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    return {l.strip().lstrip("@").lower() for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")}


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------------------------------------------------------------------------------------------- hellos
def prepare_hellos(store: Store, profile: Profile, never: set[str]) -> int:
    """A hello draft for everyone you follow who you've never had a chat with. Skips the never list, anyone whose
    username suggests they're under 19, people you've had the app unfollow, and anyone already said hello to.
    The sender also re-checks that the chat is empty right before sending."""
    templates = [t for t in profile.intro_templates if not check_text(t)]
    if not templates:
        store.log("problem", "No hello messages saved. Add some under About me.")
        return 0
    folders = {_squash(re.sub(r"_\d+$", "", r["id"])) for r in store.all("SELECT id FROM threads")}
    talked = {r["username"] for r in store.all("SELECT username FROM threads WHERE username IS NOT NULL")}
    rows = store.all("""SELECT p.username FROM people p WHERE p.i_follow = 1
        AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.kind = 'hello' AND d.username = p.username)
        AND NOT EXISTS (SELECT 1 FROM unfollows u WHERE u.username = p.username)
        ORDER BY p.followed_at DESC""")
    made = 0
    for r in rows:
        u = r["username"]
        if u in talked or _squash(u) in folders or u in never or _YOUNG_YEAR.search(u):
            continue
        text = templates[int(hashlib.sha256(u.encode()).hexdigest(), 16) % len(templates)]
        store.execute("INSERT OR IGNORE INTO drafts(kind, username, text, status, created_at, updated_at) "
                      "VALUES ('hello', ?, ?, 'draft', ?, ?)", (u, text, now_iso(), now_iso()))
        made += 1
    if made:
        store.log("info", f"Got {made} hellos ready for you to look over.")
    return made


# ---------------------------------------------------------------------------------------------- replies
def chats_waiting(store: Store, window_days: int) -> list[dict]:
    """One-to-one chats where they wrote last, recently, and that don't have a draft for that message yet."""
    since = int(_since_ms(window_days))
    # Message requests are left out: Instagram needs you to accept a request in the app before you can reply.
    return store.all("""SELECT t.* FROM threads t WHERE t.is_group = 0 AND t.is_request = 0 AND t.last_from_me = 0 AND t.last_at >= ?
        AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.kind = 'reply' AND d.thread_id = t.id AND d.replying_at = t.last_at)
        ORDER BY t.last_at DESC""", (since,))


def _since_ms(days: int) -> float:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000


def plan_replies(store: Store, profile: Profile, never: set[str], *, window_days: int = 90, manual: bool = False,
                 min_age: int = 19) -> tuple[list[dict], dict]:
    """Every chat waiting on Luke: the hard rules are applied here (never list, "stop messaging me", age), and those
    chats get their outcome straight away. Returns (jobs for the AI, counts)."""
    chats = chats_waiting(store, window_days)
    out = {"chats": len(chats), "drafted": 0, "for_you": 0, "skipped": 0, "failed": 0}
    jobs = []
    for t in chats:
        msgs = store.all("SELECT from_me, text, at FROM messages WHERE thread_id = ? ORDER BY at", (t["id"],))
        theirs = [m["text"] for m in msgs if not m["from_me"]]
        last = next(m for m in reversed(msgs) if not m["from_me"])
        base = dict(kind="reply", username=t["username"], thread_id=t["id"], other_name=t["other_name"],
                    replying_to=last["text"], replying_at=t["last_at"])
        # Hard rules first: these never reach the AI.
        if (t["username"] or "") in never:
            _draft(store, base, "", "skipped", "on your never list")
            out["skipped"] += 1
            continue
        if any(DISENGAGE.search(x) for x in theirs[-3:]):
            _draft(store, base, "", "skipped", "they asked not to be messaged")
            out["skipped"] += 1
            continue
        ages = [a for a in (stated_age(x) for x in theirs) if a is not None]
        if any(a < min_age or a > 90 for a in ages):
            _draft(store, base, "", "skipped", f"they said they're {ages[-1]}; no reply")
            _triage(store, t, {"concern": "high", "concern_why": f"said they're {ages[-1]}"})
            out["skipped"] += 1
            continue
        context = {"theirs": theirs[-3:]}
        if manual:
            _draft(store, base, "", "draft", "type your reply (turn on AI replies in Settings to get one written)",
                   {**context, "manual": True})
            out["for_you"] += 1
            continue
        jobs.append(job_for(store, t, msgs, base, context))
    return jobs, out


def job_for(store: Store, t: dict, msgs: list[dict], base: dict, context: dict) -> dict:
    """Everything the AI needs for one chat (kept small: overnight jobs are saved until the answers come back)."""
    history = [("out" if m["from_me"] else "in", m["text"]) for m in msgs[-30:]]
    person = store.one("SELECT memory, note FROM people WHERE username = ?", (t["username"],)) if t["username"] else None
    memory = json.loads(person["memory"]) if person else []
    if person and person.get("note"):
        memory = [f"Luke's own note: {person['note']}"] + memory
    theirs = [m["text"] for m in msgs if not m["from_me"]]
    return {"base": base, "context": context, "history": history, "memory": memory, "theirs": theirs[-6:],
            "thread": {"id": t["id"], "username": t["username"], "other_name": t["other_name"]}}


def own_messages(store: Store) -> list[str]:
    """Things Luke wrote himself, as style examples (never as facts)."""
    return [r["text"] for r in store.all("SELECT text FROM messages WHERE from_me = 1 AND text NOT LIKE '[%' "
                                         "ORDER BY at DESC LIMIT 60")]


def prompt_for(drafter: Drafter, profile: Profile, job: dict, own: list[str], guidance: str = "") -> tuple[list[str], str]:
    """The two-part system prompt and the user message for one chat."""
    facts = profile.relevant_facts(" ".join(job["theirs"]))
    stable, volatile = drafter.system_prompt(facts, own)
    convo = "\n".join(f"{'THEM' if d == 'in' else 'LUKE'}: {t}" for d, t in job["history"])
    mem = "\n".join(f"- {m}" for m in job["memory"][-20:]) or "(nothing yet)"
    ask = "Write Luke's next message to their latest one, and sort the conversation."
    if guidance:
        ask += ("\nLuke's own direction for this reply, from outside the conversation (follow it when it fits his "
                f"rules): {guidance}")
    user = f"What Luke remembers about them:\n{mem}\n\n<conversation>\n{convo}\n</conversation>\n\n{ask}"
    return [stable + "\n" + TRIAGE_RULES, volatile], user


def finish_reply(store: Store, profile: Profile, job: dict, raw: dict) -> bool:
    """Check what the AI wrote and save it as a draft for Luke. Returns True if it needs a proper look."""
    result = Think.model_validate(raw)
    reply = in_lukes_voice(result.reply) if result.reply else ""
    problems = check_text(reply) if reply else []
    unknown = [f for f in result.facts_used if not profile.fact(f) or profile.fact(f) not in profile.verified_facts()]
    injection = bool(INJECTION.search(job["theirs"][-1] if job["theirs"] else ""))
    needs_look = result.needs_human or bool(problems) or bool(unknown) or injection or result.concern == "high" or not reply
    note = "; ".join(dict.fromkeys(filter(None, [result.reason if result.needs_human else "", ", ".join(problems),
                                               "mentions something not in your About me" if unknown else "",
                                               "their message tries to give the AI instructions" if injection else "",
                                               result.concern_why if result.concern != "none" else ""])))
    ctx = {**job["context"], "summary": result.summary, "interest": result.interest, "interest_why": result.interest_why,
           "concern": result.concern, "concern_why": result.concern_why, "language": result.language,
           "their_message_english": result.their_message_english, "reply_english": result.reply_english,
           "needs_look": needs_look}
    _draft(store, job["base"], reply, "draft", note or None, ctx)
    _triage(store, job["thread"], result.model_dump())
    _remember(store, job["thread"]["username"], result.memory_notes)
    return needs_look


def fail_reply(store: Store, job: dict, why: str) -> None:
    _draft(store, job["base"], "", "draft", f"couldn't write this one ({why[:80]}); type it yourself",
           {**job["context"], "manual": True})


def think(store: Store, profile: Profile, llm, never: set[str], *, window_days: int = 90, manual: bool = False,
          min_age: int = 19, progress: Callable[[int, int], None] | None = None, stop: Callable[[], bool] = lambda: False) -> dict:
    """Write a reply straight away (or leave it for Luke) for every chat waiting on him. Returns counts.
    (writer.Overnight does the same through the half-price overnight service.)"""
    jobs, out = plan_replies(store, profile, never, window_days=window_days, manual=manual, min_age=min_age)
    drafter = Drafter(profile, llm)
    own = own_messages(store)
    for i, job in enumerate(jobs):
        if stop():
            break
        if progress:
            progress(i, len(jobs))
        if hasattr(llm, "last_cost_usd"):
            llm.last_cost_usd = 0.0
        try:
            system, user = prompt_for(drafter, profile, job, own)
            needs_look = finish_reply(store, profile, job, llm.complete_json(system, user, THINK_SCHEMA))
        except (AgentError, ValidationError) as e:
            fail_reply(store, job, str(e).splitlines()[0])
            out["failed"] += 1
            continue
        finally:
            _record_cost(store, llm)
        out["drafted"] += 1
        if needs_look:
            out["for_you"] += 1
    if progress:
        progress(len(jobs), len(jobs))
    summarise(store, out, manual)
    return out


def summarise(store: Store, out: dict, manual: bool) -> None:
    if manual:
        store.log("info", f"{out['for_you']} chats are waiting for your reply (AI replies are off, so the boxes are empty)" +
                  (f"; skipped {out['skipped']}" if out["skipped"] else "") + ".")
    else:
        store.log("good", f"Wrote {out['drafted']} replies" + (f", {out['for_you']} need a proper look" if out["for_you"] else "") +
                  (f", skipped {out['skipped']}" if out["skipped"] else "") +
                  (f", {out['failed']} couldn't be written" if out["failed"] else "") + ".")


def _draft(store: Store, base: dict, text: str, status: str, note: str | None, context: dict | None = None) -> None:
    """Save a reply draft. A placeholder left while it was being written overnight is filled in; anything else
    already saved for that message is kept as it is."""
    store.execute(
        "INSERT INTO drafts(kind, username, thread_id, other_name, replying_to, replying_at, text, status, note, "
        "context, created_at, updated_at) VALUES (:kind, :username, :thread_id, :other_name, :replying_to, :replying_at, "
        ":text, :status, :note, :context, :now, :now) "
        "ON CONFLICT(thread_id, replying_at) WHERE kind = 'reply' DO UPDATE SET text = excluded.text, "
        "status = excluded.status, note = excluded.note, context = excluded.context, updated_at = excluded.updated_at "
        "WHERE drafts.status = 'writing'",
        {**base, "text": text, "status": status, "note": note, "context": json.dumps(context or {}), "now": now_iso()})


def _triage(store: Store, thread: dict, r: dict) -> None:
    if not thread["username"]:
        return
    store.execute("INSERT INTO people(username, display_name, updated_at) VALUES (?, ?, ?) ON CONFLICT(username) DO NOTHING",
                  (thread["username"], thread["other_name"], now_iso()))
    store.execute("UPDATE people SET summary = COALESCE(?, summary), interest = COALESCE(?, interest), "
                  "interest_why = COALESCE(?, interest_why), concern = COALESCE(?, concern), concern_why = COALESCE(?, concern_why), "
                  "updated_at = ? WHERE username = ?",
                  (r.get("summary"), r.get("interest"), r.get("interest_why"), r.get("concern"), r.get("concern_why"),
                   now_iso(), thread["username"]))


def _remember(store: Store, username: str | None, notes: list[str]) -> None:
    if not username or not notes:
        return
    row = store.one("SELECT memory FROM people WHERE username = ?", (username,))
    mem = json.loads(row["memory"]) if row else []
    for n in notes:
        n = n.strip()[:200]
        if n and n not in mem:
            mem.append(n)
    store.execute("UPDATE people SET memory = ? WHERE username = ?", (json.dumps(mem[-30:]), username))


def _record_cost(store: Store, llm) -> None:
    store.add_spend(getattr(llm, "last_cost_usd", 0.0) or 0.0, "drafts")

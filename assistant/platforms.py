"""The social platforms Convo knows. Adding one later means adding an entry here (and, for live data, a connector):
nothing else in the app lists platforms by name.

`official` describes what the platform's official API offers, so the app can say plainly what is and isn't
available instead of guessing. Kept in plain words: the Integrations page shows these sentences.
"""
from __future__ import annotations

import re

PLATFORMS: dict[str, dict] = {
    "instagram": {
        "name": "Instagram",
        "color": "#e1306c",
        "handle": re.compile(r"^[A-Za-z0-9._]{1,30}$"),
        "profile_url": "https://www.instagram.com/{handle}/",
        "stage": 5,
        "official": {
            "connect": "Instagram API (Meta). Needs a free Creator or Business account and a Meta developer app.",
            "can": ["Followers, reach, views and profile visits", "Publishing photos, carousels and reels",
                    "Reading and replying to comments", "Replying to people who messaged you (within 24 hours)"],
            "cannot": ["Starting a DM with someone (so Hello Message keeps its careful browser method)",
                       "The list of people you follow", "Where non-followers found you, in detail"],
        },
    },
    "tiktok": {
        "name": "TikTok",
        "color": "#25f4ee",
        "handle": re.compile(r"^[A-Za-z0-9._]{2,24}$"),
        "profile_url": "https://www.tiktok.com/@{handle}",
        "stage": 5,
        "official": {
            "connect": "TikTok for Developers: Login Kit, Display API and Content Posting API.",
            "can": ["Profile and follower count", "Your videos with views, likes, comments and shares",
                    "Posting videos (private until TikTok audits the app)"],
            "cannot": ["Retention and traffic sources", "Direct messages", "Comment replies"],
        },
    },
    "youtube": {
        "name": "YouTube",
        "color": "#ff3d3d",
        "handle": re.compile(r"^[A-Za-z0-9._-]{3,30}$"),
        "profile_url": "https://www.youtube.com/@{handle}",
        "stage": 4,
        "official": {
            "connect": "YouTube Data API and YouTube Analytics API (a free Google Cloud project).",
            "can": ["Subscribers, views, watch time and retention", "Traffic sources (search, suggested, external)",
                    "Uploading videos (private until Google audits the project; about 6 a day)",
                    "Reading and replying to comments"],
            "cannot": ["Direct messages (YouTube has none)"],
        },
    },
}

ROLES = ("main", "launch", "other")
MAX_LAUNCH_ACCOUNTS = 10


def clean_handle(platform: str, handle: str) -> str:
    """The handle as stored: no leading @, no spaces, lower case. Raises ValueError."""
    p = PLATFORMS.get(platform)
    if p is None:
        raise ValueError(f"unknown platform {platform!r}")
    h = (handle or "").strip().lstrip("@")
    if not p["handle"].match(h):
        raise ValueError(f"that doesn't look like a {p['name']} username")
    return h.lower()


def profile_url(platform: str, handle: str) -> str:
    return PLATFORMS[platform]["profile_url"].format(handle=handle)

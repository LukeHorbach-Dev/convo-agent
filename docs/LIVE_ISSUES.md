# Problems found on the live Instagram account

Every problem hit during the first live runs (28 October 2026), what caused it, how it was fixed, and the test that
now reproduces it against the mock site. The mock site copies each Instagram behaviour below, so the suite fails if
a fix is ever undone.

| # | What you saw | Cause | Fix | Regression test |
|---|---|---|---|---|
| 1 | "expected element not found … div[role=dialog]" on the followers page | Instagram keeps a hidden, empty `role=dialog` earlier in the page; the app waited on that one | Every lookup uses visible elements only | mock site renders a hidden decoy dialog (all browser tests) |
| 2 | Same error after a longer wait | Loading `/<you>/followers/` directly shows only the profile in the agent's browser; the list never opens | Open your profile and click the followers/following link, like a person | mock list page shows no list unless reached from the profile |
| 3 | "0 followers seen" | The pop-up appears before its list loads | Wait for the first account, then for each new batch | `test_empty_followers_read_is_never_the_baseline` |
| 4 | (would have) introduced all 796 followers | The empty read became the baseline | An empty read is never a baseline; unknown names below known ones aren't new | `test_empty_followers_read_is_never_the_baseline`, `test_older_follower_below_known_ones_is_not_introduced` |
| 5 | Messages page: "div[role=main], a[href^=/direct/t/]" not found | Inbox rows are buttons, not links; the page uses `main[role=main]` | Click each row; only reopen rows whose preview changed | `test_inbox_rows_are_opened_only_when_they_change` |
| 6 | (would have) mixed up who said what | Inbox list and conversation share one `main` | Split left/right on the conversation pane; skip "Seen", dates, etc. | `test_instagram_message_reader_on_a_page_shaped_like_instagram` |
| 7 | (would have) failed to press Send | Send only appears after typing | Wait for it; otherwise press Enter in the message box | covered by send tests; live check pending |
| 8 | "no Message control found on the profile" | Checked before Instagram drew the profile header | Wait for Message or a "can't message" notice | mock draws the profile header late (all intro tests) |
| 9 | Read 97 / 181 / 201 instead of 1,465 | Read the followers list, then scrolling stopped early | `audience=following`; each batch: scroll into view, wheel scroll, scroll the list's own box, wait up to 8 s; one call per batch | `test_following_audience_says_hello_to_everyone_you_follow` |
| 10 | Window took half the screen | Fixed 1400×900 viewport | Real window size, maximised | n/a (cosmetic) |
| 11 | Stuck on a small chat box, "5 accounts failed with ambiguous_state" | Message opens a chat pop-up, not the conversation page | Click the pop-up's expand icon; if there isn't one, open `ig.me/m/<user>`; only then give up | mock Message opens a pop-up; `test_chat_popup_without_expand_falls_back_to_the_dm_address` |
| 12 | One account: no Message after 25 s | Deleted/renamed account ("Sorry, this page isn't available") | Treated as "can't message": skipped, doesn't trip the page-changed alarm | `test_unavailable_profile_is_skipped_not_treated_as_a_broken_page` |
| 13 | Fix for #11 "didn't work" | The update never got installed (no DONE), so the old version ran | `update.sh` installs from the zip in one step; the version is printed at startup and shown on the dashboard | `update.sh` tested on an old install with personal data |
| 14 | Canned "Sounds interesting…" replies | No AI key: the test model was writing replies | On Instagram without a key, reply boxes are left empty for you to type | `test_without_an_ai_key_real_replies_are_left_for_you_to_write` |

## Not yet seen running on the live account

* Typing and sending a message, and confirming it appeared (#7).
* Whether the chat pop-up's expand icon has the label the app looks for (#11); the `ig.me` fallback covers it if not.
* Reading a full following list of 1,465 (#9).

When any of these fails, the app stops that person safely, saves a picture in `data/diagnostics/`, and
`python -m agent inspect --platform instagram [--dm USER]` reports the page structure without sending anything.

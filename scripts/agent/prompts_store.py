"""User-editable LLM prompt storage.

Kept in ``~/.flickr_mcp/{nsid}/prompts.db`` — next to ``llm.json`` and the
OAuth credentials, deliberately outside ``flickr.db`` so editing prompts
survives a "Reset Database" (which deletes the whole ``flickr.db`` file).

Three tables: ``prompt_categories`` (some shipped, user can add more),
``prompts`` (named/described templates, each in one category, with an
optional ``{photo_id}``/``{user_nsid}``-style placeholder convention matched
by ``prompt_variables``, a reference catalog of what those placeholders mean
and where they get substituted).

Builtin rows (``builtin=1``) ship with a ``default_text``/definition so they
can be reset; they can't be deleted, only edited or reset.
"""

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from flickr_api import _CREDS_BASE

from agent import settings as _agent_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_categories (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    builtin     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prompts (
    id           TEXT PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    description  TEXT,
    category_id  TEXT NOT NULL REFERENCES prompt_categories(id),
    context      TEXT NOT NULL DEFAULT 'global',
    text         TEXT NOT NULL,
    builtin      INTEGER NOT NULL DEFAULT 0,
    default_text TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS prompt_variables (
    code        TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    description TEXT,
    resolved_by TEXT NOT NULL DEFAULT 'server',
    builtin     INTEGER NOT NULL DEFAULT 0
);
"""

# Schema migrations — each is a (check_sql, alter_sql) pair, applied on every
# connection open (mirrors agent/store.py; this file is small enough that a
# PRAGMA user_version cursor like flickr_sync.py's would be overkill).
_MIGRATIONS: list[tuple[str, str]] = []

SYSTEM_PROMPT_DEFAULT = (
    "You are the Flickr Workbench assistant. You manage the user's own Flickr "
    "account through the provided tools: their photos, albums, groups, "
    "galleries, and contacts, backed by a local database synced from Flickr.\n"
    "The logged-in account is NSID {user_nsid}, currently using the username "
    "'{username}' (this is the name that appears in flickr.com URLs, e.g. "
    "flickr.com/photos/{username}/...).\n"
    "Guidelines:\n"
    "- The NSID is the only identifier that's guaranteed stable — usernames "
    "can be renamed at any time. If a URL, profile, or comment mentions a "
    "username that looks unfamiliar or different from '{username}', don't "
    "assume it's a different person: call get_person_info on it (it accepts "
    "a username, NSID, or profile/photo URL) and check its `is_you` field "
    "before concluding whether it belongs to this account or someone else.\n"
    "- Read current state before changing anything (e.g. get_photo before "
    "update_photo).\n"
    "- Propose changes and wait for the user's go-ahead; write tools "
    "additionally require an explicit confirmation in the UI, and a declined "
    "confirmation means skip it and move on.\n"
    "- When suggesting groups or albums, use numbered lists so the user can "
    "pick by number.\n"
    "- Suggested tags: lowercase, compound words concatenated (oakpark), "
    "never bare year tags.\n"
    "- Keep responses concise. When you discuss a specific photo, mention its "
    "photo id.\n"
    "- Some turns include a note naming the photo currently open in the "
    "user's Photo Browser panel. Treat that as the default target for "
    "instructions that don't name a different photo — but an explicit photo "
    "id or link in the user's own message always takes priority over it. "
    "That note only gives an id, not details — if the user asks about 'the "
    "current photo' or similar, call get_photo (or another relevant tool) "
    "for that id to get fresh data rather than recalling an earlier photo "
    "from this conversation's history.\n"
    "- You CAN change what the user sees in the Photo Browser panel: calling "
    "any tool with a photo id (get_photo is the cheapest) switches that panel "
    "to show that photo. Whenever the user asks to see, open, switch to, or "
    "pull up a specific photo by id, call get_photo for it — do not claim you "
    "have no way to control the Photo Browser.\n"
    "- When the user says 'remember' or 'memory' followed by guidance, or asks "
    "you to remember a preference or rule for future conversations, call the "
    "`remember` tool with that guidance. Keep each piece of guidance as a "
    "concise, self-contained sentence or rule.\n"
    "- CRITICAL: Never claim to have seen, viewed, or visually described a "
    "photo unless actual image data was provided in the tool result. If a tool "
    "result says vision is disabled, work from title, description, tags, and "
    "EXIF only, and tell the user explicitly that visual inspection is "
    "unavailable. Guessing or fabricating visual details is not allowed.\n"
    "- CRITICAL: Tool schemas are provided to you exactly as they are. Never "
    "claim a tool doesn't support a field, parameter, or capability without "
    "rechecking its schema first — if it's in the schema, it's supported. "
    "Never state you performed an action, or that a specific field was "
    "updated, without checking the actual arguments you sent in that tool "
    "call. If you made a mistake (e.g. left a field out of an update), say so "
    "plainly instead of inventing an explanation for why it couldn't be done."
)

GROUP_SUMMARY_PROMPT_DEFAULT = (
    "You are cataloging a Flickr group for photo-sharing automation.\n\n"
    "Group name: {group_name}\n\n"
    "Group description (rules/restrictions, as written by the group admin):\n"
    "{group_description}\n\n"
    "The user's own note about this group (may be empty):\n"
    "{group_user_note}\n\n"
    "Reply with ONLY a JSON object (no markdown code fences, no commentary) with these keys:\n"
    "- \"summary\": a short markdown summary (2-5 sentences) of what the group is about, "
    "and any posting rules or restrictions (limits on photos per day/week, required themes, "
    "content restrictions, etc). Incorporate the user's note above where relevant.\n"
    "- \"is_milestone\": true if this is a \"milestone\"/threshold group that only accepts "
    "photos once they reach a minimum view or favorite count, else false.\n"
    "- \"fave_min\": integer minimum favorite count required to post, or null if none/not applicable.\n"
    "- \"view_min\": integer minimum view count required to post, or null if none/not applicable.\n"
    "- \"open_subject\": true if the group accepts photos of any subject (no theme restriction), "
    "false if it's restricted to a specific theme or subject (e.g. \"black and white only\", "
    "\"nature only\").\n"
    "- \"keywords\": a list of 5-15 lowercase keywords and synonyms describing the group's theme, "
    "useful for search.\n"
)

COMPACT_PROMPT_DEFAULT = (
    "Summarize this entire conversation so it can continue seamlessly without "
    "the full history above. Capture what the user asked for and why, "
    "decisions made, any photo/album/group ids or titles referenced, and "
    "anything left unresolved. Be concise but keep the specifics the "
    "assistant will need to keep working correctly. Write the summary "
    "itself, not a description of writing one."
)

_STYLE_RULES = (
    "Style rules: never include year tags (e.g. '2007'); compound tags are "
    "concatenated lowercase (oakpark, not oak-park); don't add location tags "
    "like oakpark/chicago unless the subject is location-relevant; never "
    "include self-promotional URLs."
)

_SEED_CATEGORIES = [
    ("system", "System", "Core agent behavior and standing memory.", 0),
    ("own_photo", "My Photo", "Prompts about a single photo you own.", 1),
    ("other_photo", "Someone Else's Photo",
     "Prompts for photos you don't own — fave/comment style workflows.", 2),
    ("collection", "My Collection",
     "Prompts that operate across your whole photo library — weak-photo "
     "review, threshold/boost groups, unearthing private photos, replying "
     "to comments.", 3),
]

# Categories aren't user-editable — a fixed, known set, so the system (not
# the user) can use a prompt's category to decide which page its workflow
# button belongs on. "photo" categories surface in the Photo Viewer panel;
# everything else surfaces in the global Chat/Command Palette.
_CATEGORY_CONTEXT = {
    "system": "global",
    "own_photo": "photo",
    "other_photo": "photo",
    "collection": "global",
}


def _context_for_category(category_id: str) -> str:
    return _CATEGORY_CONTEXT.get(category_id, "global")


_SEED_VARIABLES = [
    ("photo_id", "Photo ID", "The photo in context. Substituted client-side "
     "from the selected photo before the prompt is sent.", "client"),
    ("user_nsid", "Your Flickr NSID", "Your own Flickr user ID — the stable "
     "identifier that never changes even if your username does. Substituted "
     "server-side from your logged-in credentials.", "server"),
    ("username", "Your Flickr username", "Your current Flickr username/URL "
     "path alias. This can change over time (renaming your account), so old "
     "links or messages may reference a previous username for the same "
     "account. Substituted server-side from your logged-in credentials.",
     "server"),
    ("group_name", "Group name", "The joined group's name. Substituted by "
     "the background groups sync before calling the LLM.", "server"),
    ("group_description", "Group description", "The joined group's Flickr "
     "description (rules/restrictions). Substituted by the background "
     "groups sync before calling the LLM.", "server"),
    ("group_user_note", "Your note about the group", "Your own freeform "
     "note about the group (set via the set_group_note tool). Substituted "
     "by the background groups sync before calling the LLM.", "server"),
]

_SEED_PROMPTS = [
    dict(code="system-core", name="System prompt", category_id="system",
         context="global", text=SYSTEM_PROMPT_DEFAULT,
         description="Core behavior contract sent as the first system "
         "message on every turn."),
    dict(code="user-memory", name="Standing memory", category_id="system",
         context="global", text="",
         description="Accumulated guidance saved via the `remember` tool "
         "or edited here; sent as a second system message on every turn."),
    dict(code="compact-conversation", name="Compact conversation", category_id="system",
         context="global", text=COMPACT_PROMPT_DEFAULT,
         description="Instruction sent to the LLM to summarize a conversation "
         "when compacting it, replacing its stored history in place — used "
         "by both the manual \"Compact now\" action and auto-compact."),
    dict(code="group-summary", name="Group summary", category_id="system",
         context="global", text=GROUP_SUMMARY_PROMPT_DEFAULT,
         description="Used by the background groups sync to (re)generate a "
         "joined group's AI summary, milestone thresholds, and search "
         "keywords whenever its name, description, or your note changes. "
         "Sent alone in a fresh, tool-free call — not launchable from chat."),
    dict(code="improve-photo", name="Improve metadata", category_id="own_photo",
         context="photo", description="Suggest title/description/tags for "
         "the current photo.",
         text=(
             "Review my photo {photo_id}. If you don't already have its image and "
             "current metadata from earlier in this conversation, fetch it first "
             "with fetch_photo_image and get_photo — never overwrite without "
             "reading first. Then suggest a concise descriptive title, a 1-2 "
             "sentence description capturing mood and subject, and relevant tags. "
             + _STYLE_RULES + " Show your suggestions and wait for my confirmation "
             "before calling update_photo."
         )),
    dict(code="suggest-groups", name="Suggest groups", category_id="own_photo",
         context="photo", description="Suggest Flickr groups for the current photo.",
         text=(
             "Look at my photo {photo_id}. If you don't already have its image and "
             "group memberships from earlier in this conversation, fetch it first "
             "(fetch_photo_image, then get_photo and get_photo_contexts to see "
             "which groups it's already in). Search my joined groups with "
             "find_groups using 2-3 keyword searches based on the photo's subject "
             "and location, and also check get_group_stats with limit=100 to "
             "browse by membership size. Suggest up to 5 relevant groups it's not "
             "in yet as a NUMBERED list and wait for me to pick numbers. Only add "
             "the groups whose numbers I pick, with add_to_group."
         )),
    dict(code="suggest-albums", name="Suggest albums", category_id="own_photo",
         context="photo", description="Suggest albums for the current photo.",
         text=(
             "Check which albums my photo {photo_id} should be in. If you don't "
             "already have its image and current albums from earlier in this "
             "conversation, fetch it first (fetch_photo_image, then "
             "get_photo_contexts for current albums) and find_albums to see what "
             "exists. Suggest topical albums that match the subject as a numbered "
             "list — but don't suggest more once the photo is already in about 5 "
             "albums, and never suggest the 'Made Explore' album unless I confirm "
             "the photo made Explore. Wait for my picks, then add with "
             "add_to_album."
         )),
    dict(code="threshold-groups", name="Threshold groups", category_id="own_photo",
         context="photo", description="Check the current photo against "
         "view/fave threshold group requirements.",
         text=(
             "Check if my photo {photo_id} qualifies for view/fave threshold "
             "groups. If you don't already have its stats and group contexts "
             "from earlier in this conversation, get them with get_photo_stats "
             "and get_photo_contexts. Then compare its stats against these joined "
             "groups, skipping any it's already in: 10,000 Views Unlimited "
             "(2337493@N25, 10k+ views), 5,000 Views (48333387@N00, 5k+ views), "
             "2,000 Views Unlimited (2337875@N25, 2k+ views), Flickr's Finest "
             "100+ Faves (910466@N22), 100 faves minimum (14707878@N20), 50+ "
             "Favorites (2888626@N21), 250 faves (2838082@N25), The Flickr "
             "Collection (778902@N24, 250+ faves). List qualifying groups as a "
             "numbered list and wait for my picks before add_to_group."
         )),
    dict(code="suggest-comment-fave", name="Suggest a comment & like", category_id="other_photo",
         context="photo", description="Suggest a comment for a photo you don't "
         "own, then fave and post it once you confirm.",
         text=(
             "Look at photo {photo_id}, which isn't mine. Fetch it with "
             "fetch_photo_image and get_photo if you don't already have its image "
             "and metadata from earlier in this conversation. Suggest one short, "
             "genuine-sounding comment (not generic praise — reference something "
             "specific about the photo). Show it and wait for my go-ahead or edits "
             "before calling fave_photo and add_comment."
         )),
    dict(code="other-photo-owner", name="About the creator", category_id="other_photo",
         context="photo", description="Look up the owner of a photo you don't "
         "own, and your relationship to them.",
         text=(
             "Look up who owns photo {photo_id} (call get_photo if you don't "
             "already have its owner from earlier in this conversation), then call "
             "get_person_info on their NSID. Summarize: their name/location/bio, "
             "how many photos they have, and — most importantly — our relationship: "
             "do I follow them, do they follow me, and are they marked as a friend "
             "or family contact."
         )),
    dict(code="other-photo-groups", name="Group status", category_id="other_photo",
         context="photo", description="Check the groups a photo you don't own "
         "belongs to: joined or not, popularity, description.",
         text=(
             "Find the group pools photo {photo_id} belongs to with "
             "get_photo_contexts, then call get_group_info on each one. For every "
             "group, report: its name, whether I've already joined it, its member "
             "and pool-photo counts (popularity), and a short summary of its "
             "description/rules."
         )),
    dict(code="reply-comments", name="Reply to comments", category_id="collection",
         context="global", description="Draft replies to unanswered comments "
         "across your photos.",
         text=(
             "Help me reply to recent comments on my photos. Call "
             "get_photos_with_comments (limit=30), then get_photo_comments for "
             "each. My NSID is {user_nsid}: a comment already has a reply if one "
             "of my comments appears after it in the thread. Reply to group "
             "notification comments too, not just personal ones. For each "
             "unanswered comment, one at a time: show the photo title, commenter, "
             "and comment text, then suggest 5 short reply options with variety "
             "(emoji-only, brief thanks, specific, warm, casual), each formatted "
             "as '[<author_url>] <message>' using author_url from the comment "
             "data. Wait for me to pick or write my own before posting with "
             "add_comment."
         )),
    dict(code="weak-photos", name="Review weak photos", category_id="collection",
         context="global", description="Find and review your weakest photos "
         "one at a time.",
         text=(
             "Find my weakest photos with find_weak_photos "
             "(require_zero_favorites=true, limit=30). Take the top candidate "
             "not yet reviewed in this conversation: fetch it with "
             "fetch_photo_image, give an honest visual assessment (composition, "
             "light, subject, technical quality — early smartphone photos get "
             "more latitude), and recommend keep-public or make-private. Wait "
             "for my decision. If private: suggest title/description/tags, apply "
             "with update_photo, then set_visibility to private. If keep: "
             "suggest improved metadata and ask about groups. "
             + _STYLE_RULES
         )),
    dict(code="unearth-private", name="Unearth private photos", category_id="collection",
         context="global", description="Find old private photos and decide "
         "which ones to publish.",
         text=(
             "Search my private photos with search_photos (is_public=false, "
             "sort_by=date_taken, order=asc, limit=50) to find the oldest ones "
             "not yet reviewed in this conversation. Take the top candidate: "
             "fetch it with fetch_photo_image, give an honest visual "
             "assessment (composition, light, subject, technical quality — "
             "early smartphone photos get more latitude), and recommend "
             "publish or keep-private. Wait for my decision. If publish: "
             "suggest title/description/tags, apply with update_photo, then "
             "set_visibility to public, and suggest a couple of relevant "
             "groups with get_group_stats. If keep private: move to the next "
             "candidate. "
             + _STYLE_RULES
         )),
]


def _prompts_db_path(nsid: str) -> str:
    return os.path.join(_CREDS_BASE, nsid, "prompts.db")


@contextmanager
def _prompts_db(nsid: str):
    path = _prompts_db_path(nsid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    for check_sql, alter_sql in _MIGRATIONS:
        if conn.execute(check_sql).fetchone() is None:
            conn.execute(alter_sql)
    _seed_defaults(conn, nsid)
    _sync_builtin_defaults(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _seed_defaults(conn: sqlite3.Connection, nsid: str) -> None:
    """Insert builtin categories/prompts/variables if this is a fresh DB.

    Carries over an existing user's ``base_prompt`` (from llm.json) into the
    new ``user-memory`` row so no one's saved memory is lost in the move.
    """
    if conn.execute("SELECT 1 FROM prompt_categories LIMIT 1").fetchone():
        return
    now = int(time.time())
    conn.executemany(
        "INSERT INTO prompt_categories (id, name, description, sort_order, builtin) "
        "VALUES (?, ?, ?, ?, 1)",
        _SEED_CATEGORIES,
    )
    conn.executemany(
        "INSERT INTO prompt_variables (code, label, description, resolved_by, builtin) "
        "VALUES (?, ?, ?, ?, 1)",
        _SEED_VARIABLES,
    )

    legacy_memory = ""
    try:
        legacy_memory = (_agent_settings.load_settings(nsid).get("base_prompt") or "").strip()
    except Exception:
        pass

    for i, p in enumerate(_SEED_PROMPTS):
        text = p["text"]
        if p["code"] == "user-memory" and legacy_memory:
            text = legacy_memory
        conn.execute(
            "INSERT INTO prompts (id, code, name, description, category_id, context, "
            "text, builtin, default_text, enabled, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?, ?)",
            (uuid.uuid4().hex, p["code"], p["name"], p.get("description", ""),
             p["category_id"], p["context"], text, p["text"], i, now, now),
        )


def _sync_builtin_defaults(conn: sqlite3.Connection) -> None:
    """Keep builtin category/prompt/variable definitions in step with code changes.

    ``_seed_defaults`` only inserts rows once, when a user's DB is brand new —
    it never revisits an existing DB, so a category, prompt, or variable added
    in a later release would otherwise never reach users who logged in before
    that release. Runs on every connection open:
    - Inserts any builtin category in ``_SEED_CATEGORIES`` or variable in
      ``_SEED_VARIABLES`` missing from an existing DB (``INSERT OR IGNORE``,
      so it's a no-op once present) — categories first, since prompts below
      reference them.
    - Inserts any builtin prompt in ``_SEED_PROMPTS`` whose code doesn't
      exist yet for this user (same shape ``_seed_defaults`` would have used
      for a brand-new user), so a new builtin prompt reaches existing users
      too instead of only ever appearing for accounts created after it shipped.
    - Refreshes a builtin prompt's ``default_text`` whenever the shipped
      default in ``_SEED_PROMPTS`` changes, and carries that change into the
      live ``text`` too — but only if the user hasn't diverged their own
      edited ``text`` away from the old default already.
    """
    conn.executemany(
        "INSERT OR IGNORE INTO prompt_categories (id, name, description, sort_order, builtin) "
        "VALUES (?, ?, ?, ?, 1)",
        _SEED_CATEGORIES,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO prompt_variables (code, label, description, resolved_by, builtin) "
        "VALUES (?, ?, ?, ?, 1)",
        _SEED_VARIABLES,
    )
    now = int(time.time())
    for p in _SEED_PROMPTS:
        row = conn.execute(
            "SELECT text, default_text FROM prompts WHERE code = ? AND builtin = 1",
            (p["code"],),
        ).fetchone()
        if row is None:
            sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM prompts WHERE category_id = ?",
                (p["category_id"],),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO prompts (id, code, name, description, category_id, context, "
                "text, builtin, default_text, enabled, sort_order, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?, ?)",
                (uuid.uuid4().hex, p["code"], p["name"], p.get("description", ""),
                 p["category_id"], p["context"], p["text"], p["text"], sort_order, now, now),
            )
            continue
        if row["default_text"] == p["text"]:
            continue
        new_text = p["text"] if row["text"] == row["default_text"] else row["text"]
        conn.execute(
            "UPDATE prompts SET text = ?, default_text = ?, updated_at = ? WHERE code = ?",
            (new_text, p["text"], now, p["code"]),
        )


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for key in ("builtin", "enabled"):
        if key in d:
            d[key] = bool(d[key])
    return d


# ── Categories ────────────────────────────────────────────────────────────


def list_categories(nsid: str) -> list[dict]:
    with _prompts_db(nsid) as conn:
        rows = conn.execute(
            "SELECT * FROM prompt_categories ORDER BY sort_order, name"
        ).fetchall()
    return [_row(r) for r in rows]


def create_category(nsid: str, name: str, description: str = "") -> dict:
    cat_id = uuid.uuid4().hex
    with _prompts_db(nsid) as conn:
        sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM prompt_categories"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO prompt_categories (id, name, description, sort_order, builtin) "
            "VALUES (?, ?, ?, ?, 0)",
            (cat_id, name, description, sort_order),
        )
    return {"id": cat_id, "name": name, "description": description,
            "sort_order": sort_order, "builtin": False}


def update_category(nsid: str, category_id: str, name: str | None = None,
                     description: str | None = None) -> dict | None:
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT * FROM prompt_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE prompt_categories SET name = ?, description = ? WHERE id = ?",
            (name if name is not None else row["name"],
             description if description is not None else row["description"],
             category_id),
        )
        updated = conn.execute(
            "SELECT * FROM prompt_categories WHERE id = ?", (category_id,)
        ).fetchone()
    return _row(updated)


def delete_category(nsid: str, category_id: str) -> tuple[bool, str | None]:
    """Returns (ok, error). Refuses builtin categories or ones still in use."""
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT builtin FROM prompt_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            return False, "category not found"
        if row["builtin"]:
            return False, "built-in categories can't be deleted"
        in_use = conn.execute(
            "SELECT 1 FROM prompts WHERE category_id = ? LIMIT 1", (category_id,)
        ).fetchone()
        if in_use:
            return False, "category still has prompts assigned to it"
        conn.execute("DELETE FROM prompt_categories WHERE id = ?", (category_id,))
    return True, None


# ── Prompts ───────────────────────────────────────────────────────────────


def list_prompts(nsid: str, category_id: str | None = None) -> list[dict]:
    with _prompts_db(nsid) as conn:
        if category_id:
            rows = conn.execute(
                "SELECT * FROM prompts WHERE category_id = ? ORDER BY sort_order, name",
                (category_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM prompts ORDER BY category_id, sort_order, name"
            ).fetchall()
    return [_row(r) for r in rows]


def get_prompt(nsid: str, prompt_id: str) -> dict | None:
    with _prompts_db(nsid) as conn:
        row = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
    return _row(row) if row else None


def get_prompt_by_code(nsid: str, code: str) -> dict | None:
    with _prompts_db(nsid) as conn:
        row = conn.execute("SELECT * FROM prompts WHERE code = ?", (code,)).fetchone()
    return _row(row) if row else None


def create_prompt(nsid: str, code: str, name: str, category_id: str,
                   text: str, description: str = "") -> dict:
    prompt_id = uuid.uuid4().hex
    now = int(time.time())
    context = _context_for_category(category_id)
    with _prompts_db(nsid) as conn:
        sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM prompts WHERE category_id = ?",
            (category_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO prompts (id, code, name, description, category_id, context, "
            "text, builtin, default_text, enabled, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, 1, ?, ?, ?)",
            (prompt_id, code, name, description, category_id, context, text,
             sort_order, now, now),
        )
    return get_prompt(nsid, prompt_id)


def update_prompt(nsid: str, prompt_id: str, **fields) -> dict | None:
    """Update any of name/description/category_id/text/enabled.

    ``context`` isn't user-settable — it's derived from ``category_id``
    whenever the category changes, so the system (not the user) decides
    which page a prompt's workflow button belongs on.
    """
    allowed = {"name", "description", "category_id", "text", "enabled"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_prompt(nsid, prompt_id)
    if "category_id" in updates:
        updates["context"] = _context_for_category(updates["category_id"])
    with _prompts_db(nsid) as conn:
        row = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
        if not row:
            return None
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE prompts SET {set_clause}, updated_at = ? WHERE id = ?",
            (*updates.values(), int(time.time()), prompt_id),
        )
    return get_prompt(nsid, prompt_id)


def delete_prompt(nsid: str, prompt_id: str) -> tuple[bool, str | None]:
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT builtin FROM prompts WHERE id = ?", (prompt_id,)
        ).fetchone()
        if not row:
            return False, "prompt not found"
        if row["builtin"]:
            return False, "built-in prompts can't be deleted — use reset instead"
        conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
    return True, None


def reset_prompt(nsid: str, prompt_id: str) -> dict | None:
    """Restore a builtin prompt's text to its shipped default_text."""
    with _prompts_db(nsid) as conn:
        row = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
        if not row or not row["builtin"]:
            return None
        conn.execute(
            "UPDATE prompts SET text = default_text, updated_at = ? WHERE id = ?",
            (int(time.time()), prompt_id),
        )
    return get_prompt(nsid, prompt_id)


def append_user_memory(nsid: str, guidance: str) -> str:
    """Append guidance to the user-memory prompt's text. Returns the new text."""
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT id, text FROM prompts WHERE code = 'user-memory'"
        ).fetchone()
        existing = (row["text"] or "").strip() if row else ""
        updated = (existing + "\n" + guidance).strip() if existing else guidance
        conn.execute(
            "UPDATE prompts SET text = ?, updated_at = ? WHERE id = ?",
            (updated, int(time.time()), row["id"]),
        )
    return updated


# ── Variables ─────────────────────────────────────────────────────────────


def resolve_server_variables(text: str, nsid: str, username: str) -> str:
    """Substitute server-resolved placeholders ({user_nsid}, {username}) into *text*.

    Shared by the system prompt (agent/loop.py) and the workflow command list
    (agent/commands.py) so both stay in sync with prompt_variables' contract.
    """
    return text.replace("{user_nsid}", nsid).replace("{username}", username)


def list_variables(nsid: str) -> list[dict]:
    with _prompts_db(nsid) as conn:
        rows = conn.execute("SELECT * FROM prompt_variables ORDER BY code").fetchall()
    return [_row(r) for r in rows]


def create_variable(nsid: str, code: str, label: str, description: str = "") -> dict:
    with _prompts_db(nsid) as conn:
        conn.execute(
            "INSERT INTO prompt_variables (code, label, description, resolved_by, builtin) "
            "VALUES (?, ?, ?, 'documentation only', 0)",
            (code, label, description),
        )
    return {"code": code, "label": label, "description": description,
            "resolved_by": "documentation only", "builtin": False}


def delete_variable(nsid: str, code: str) -> tuple[bool, str | None]:
    with _prompts_db(nsid) as conn:
        row = conn.execute(
            "SELECT builtin FROM prompt_variables WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            return False, "variable not found"
        if row["builtin"]:
            return False, "built-in variables can't be deleted"
        conn.execute("DELETE FROM prompt_variables WHERE code = ?", (code,))
    return True, None


# ── Bulk fetch for the API ───────────────────────────────────────────────


def all_data(nsid: str) -> dict:
    return {
        "categories": list_categories(nsid),
        "prompts": list_prompts(nsid),
        "variables": list_variables(nsid),
    }

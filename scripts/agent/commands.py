"""Workflow command templates for the workbench.

Adapted from the Claude Code slash commands in ``.claude/commands/``.  Each
command is a parameterized chat prompt: photo-context commands contain a
``{photo_id}`` placeholder the frontend fills from the selected photo before
sending the prompt to the chat agent.
"""

_STYLE_RULES = (
    "Style rules: never include year tags (e.g. '2007'); compound tags are "
    "concatenated lowercase (oakpark, not oak-park); don't add location tags "
    "like oakpark/chicago unless the subject is location-relevant; never "
    "include self-promotional URLs."
)

COMMANDS: list[dict] = [
    {
        "id": "improve-photo",
        "label": "Improve metadata",
        "context": "photo",
        "prompt": (
            "Review my photo {photo_id}. First fetch it with fetch_photo_image and "
            "get its current metadata with get_photo — never overwrite without "
            "reading first. Then suggest a concise descriptive title, a 1-2 "
            "sentence description capturing mood and subject, and relevant tags. "
            + _STYLE_RULES + " Show your suggestions and wait for my confirmation "
            "before calling update_photo."
        ),
    },
    {
        "id": "suggest-groups",
        "label": "Suggest groups",
        "context": "photo",
        "prompt": (
            "Look at my photo {photo_id} (fetch_photo_image, then get_photo and "
            "get_photo_contexts to see which groups it's already in). Use "
            "get_group_stats with limit=100 and pick relevant groups by name — "
            "do not use find_groups, its keyword search is broken. Suggest up to "
            "5 relevant groups it's not in yet as a NUMBERED list and wait for me "
            "to pick numbers. Only add the groups whose numbers I pick, with "
            "add_to_group."
        ),
    },
    {
        "id": "suggest-albums",
        "label": "Suggest albums",
        "context": "photo",
        "prompt": (
            "Check which albums my photo {photo_id} should be in. Fetch it with "
            "fetch_photo_image, then get_photo_contexts for current albums and "
            "find_albums to see what exists. Suggest topical albums that match "
            "the subject as a numbered list — but don't suggest more once the "
            "photo is already in about 5 albums, and never suggest the 'Made "
            "Explore' album unless I confirm the photo made Explore. Wait for my "
            "picks, then add with add_to_album."
        ),
    },
    {
        "id": "threshold-groups",
        "label": "Threshold groups",
        "context": "photo",
        "prompt": (
            "Check if my photo {photo_id} qualifies for view/fave threshold "
            "groups. Get its stats with get_photo_stats, then compare against "
            "these joined groups: 10,000 Views Unlimited (2337493@N25, 10k+ "
            "views), 5,000 Views (48333387@N00, 5k+ views), 2,000 Views "
            "Unlimited (2337875@N25, 2k+ views), Flickr's Finest 100+ Faves "
            "(910466@N22), 100 faves minimum (14707878@N20), 50+ Favorites "
            "(2888626@N21), 250 faves (2838082@N25), The Flickr Collection "
            "(778902@N24, 250+ faves). Use get_photo_contexts to skip groups "
            "it's already in. List qualifying groups as a numbered list and wait "
            "for my picks before add_to_group."
        ),
    },
    {
        "id": "reply-comments",
        "label": "Reply to comments",
        "context": "global",
        "prompt": (
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
        ),
    },
    {
        "id": "weak-photos",
        "label": "Review weak photos",
        "context": "global",
        "prompt": (
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
        ),
    },
]


def commands_for_api(user_nsid: str) -> list[dict]:
    """Return the command list with user-level placeholders resolved."""
    return [
        {**c, "prompt": c["prompt"].replace("{user_nsid}", user_nsid)}
        for c in COMMANDS
    ]

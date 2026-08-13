"""Tool schema conversion and the write-tool registry."""

from mcp.types import Tool

import mcp_tools

# Tools that mutate Flickr or local state: the agent loop pauses the stream
# and waits for explicit user approval before executing any of these.
WRITE_TOOLS: frozenset[str] = frozenset({
    # Flickr-mutating
    "update_photo", "add_comment", "delete_comment",
    "fave_photo", "remove_fave",
    "set_visibility", "set_location", "remove_location",
    "set_safety_level", "set_content_type", "set_dates",
    "add_to_album", "remove_from_album",
    "create_album", "edit_album", "delete_album",
    "add_to_group", "remove_from_group", "join_group", "leave_group",
    "create_gallery", "add_to_gallery",
    "follow_contact", "unfollow_contact",
    # Local-DB writes
    "remove_from_queue", "set_group_note",
    "protect_contact", "add_to_never_follow",
    "add_to_keeper_list", "remove_from_keeper_list",
    # Long-running
    "sync",
})

_unknown = WRITE_TOOLS - set(mcp_tools._HANDLERS)
assert not _unknown, f"WRITE_TOOLS entries with no handler: {_unknown}"

# A curated subset of the full tool catalog, for connections whose model is
# small/local and struggles with a large tool list — every extra schema in
# context is a chance to pick the wrong tool or skip a required one. This is
# exactly the set of tools actually referenced by the built-in prompt
# playbooks in default-prompts.md, i.e. what the chat UI's canned workflows
# need day to day; the rarer contact/follow-management and album/group CRUD
# tools (mostly used by the Claude Code skills, not chat) are left out.
# Hand-picked for now — see settings.py's "tool_set" field for the switch
# that selects this set, and TOOLS.md/CLAUDE.md's MCP Tools table for the
# full catalog this is drawn from. May grow an editable-in-the-UI form later.
LIMITED_TOOL_NAMES: frozenset[str] = frozenset({
    "search_photos", "get_photo", "get_photo_stats", "get_photo_comments",
    "get_unreplied_comments", "fetch_photo_image", "update_photo", "set_visibility",
    "find_weak_photos", "add_comment", "fave_photo", "find_albums", "add_to_album",
    "find_groups", "add_to_group", "get_photo_contexts", "get_group_info", "sync",
})

_unknown_limited = LIMITED_TOOL_NAMES - set(mcp_tools._HANDLERS)
assert not _unknown_limited, f"LIMITED_TOOL_NAMES entries with no handler: {_unknown_limited}"


def all_tools() -> list[Tool]:
    tools = []
    for mod in mcp_tools._ALL_MODULES:
        tools.extend(mod.TOOLS)
    return tools


def to_openai_tools(tool_set: str = "all") -> list[dict]:
    """Convert mcp.types.Tool definitions to OpenAI function-calling format.

    ``tool_set`` is a connection's persisted choice (see settings.py):
    ``"limited"`` restricts to LIMITED_TOOL_NAMES, anything else (including
    the default ``"all"``) returns the full catalog.
    """
    tools = all_tools()
    if tool_set == "limited":
        tools = [t for t in tools if t.name in LIMITED_TOOL_NAMES]
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in tools
    ]

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


def all_tools() -> list[Tool]:
    tools = []
    for mod in mcp_tools._ALL_MODULES:
        tools.extend(mod.TOOLS)
    return tools


def to_openai_tools() -> list[dict]:
    """Convert mcp.types.Tool definitions to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in all_tools()
    ]

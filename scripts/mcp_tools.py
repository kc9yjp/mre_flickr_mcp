"""MCP server instance, tool definitions, and tool dispatch."""

import logging

from mcp.server import Server
from mcp.types import TextContent

import flickr_api
from db import db  # noqa: F401 — imported so tests can patch mcp_tools.db
from flickr_api import _api_get, _api_post, _load_credentials, _load_env  # noqa: F401
from tools import albums, contacts, galleries, groups, photos
from tools import sync as sync_tools

server = Server("flickr")

# Re-export sync infrastructure so web.py can import from here without change.
SYNC_SCRIPT = sync_tools.SYNC_SCRIPT
_sync_lock = sync_tools._sync_lock
_active_syncs = sync_tools._active_syncs
_run_sync_script = sync_tools._run_sync_script
_background_refresh = sync_tools._background_refresh

_ALL_MODULES = [photos, albums, groups, contacts, galleries, sync_tools]

_HANDLERS: dict = {}
for _mod in _ALL_MODULES:
    _HANDLERS.update(_mod.HANDLERS)


@server.list_tools()
async def list_tools():
    tools = []
    for mod in _ALL_MODULES:
        tools.extend(mod.TOOLS)
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    handler = _HANDLERS.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        return await handler(arguments)
    except (FileNotFoundError, RuntimeError) as e:
        return [TextContent(type="text", text=str(e))]
    except Exception as e:
        logging.exception("Unexpected error in tool %s", name)
        return [TextContent(type="text", text=f"Unexpected error: {type(e).__name__}")]


# Re-export handler functions so tests can call mcp_tools._search_photos etc.
from tools.photos import (  # noqa: E402, F401
    _search_photos, _get_photo, _get_summary, _list_recent_syncs, _update_photo,
    _fetch_photo_image, _get_photo_comments, _add_comment, _delete_comment,
    _fave_photo, _remove_fave, _get_photo_stats, _find_weak_photos,
    _set_visibility, _set_location, _remove_location, _set_safety_level,
    _set_content_type, _set_dates, _get_exif, _get_upload_status,
    _get_person_info, _get_photostream_stats, _get_popular_photos,
    _get_faves, _get_recent_activity,
)
from tools.albums import (  # noqa: E402, F401
    _find_albums, _get_album_photos, _add_to_album, _remove_from_album,
    _create_album, _edit_album, _delete_album,
)
from tools.groups import (  # noqa: E402, F401
    _find_groups, _set_group_keywords, _add_to_group, _remove_from_group,
    _join_group, _leave_group, _get_group_photos, _search_all_groups,
)
from tools.contacts import (  # noqa: E402, F401
    _get_contacts_summary, _find_unfollow_candidates, _protect_contact,
    _unfollow_contact, _get_contact_uploads,
)
from tools.galleries import (  # noqa: E402, F401
    _get_galleries, _create_gallery, _add_to_gallery, _get_gallery_photos,
)
from tools.sync import _sync  # noqa: E402, F401

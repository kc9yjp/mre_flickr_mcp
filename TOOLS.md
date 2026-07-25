# MCP Tools

Full catalog of the 64 MCP tools exposed by this server, grouped by the
module that defines them under `scripts/tools/`. See
[ARCHITECTURE.md](ARCHITECTURE.md#mcp-tool-layer) for how these are
aggregated, dispatched, and threaded.

Every tool here is available identically to:

- **AI clients** connected over MCP (`/sse`, `/mcp`, or stdio),
- the **Workbench chat agent** (`scripts/agent/`), which calls the same
  handlers in-process, and
- the **workflow command buttons** in the Workbench Commands panel, which
  are prompt templates that ask the chat agent to use these tools.

**Write** column: tools marked ✍️ mutate Flickr or local state. When called
from the Workbench chat agent, these pause the conversation for explicit
user approval (`schema.WRITE_TOOLS`, see
[ARCHITECTURE.md](ARCHITECTURE.md#the-workbench)) before executing. When
called from an MCP client, approval is whatever that client's own tool-use
confirmation UI provides.

---

## Photos (`tools/photos.py`) — 31 tools

| Tool | Write | Parameters | Description |
|---|:---:|---|---|
| `search_photos` | | *(all optional: `query`, `tag`, `date_from`, `date_to`, `sort`, `limit`)* | Search and filter the photo collection. Supports keyword search on title, tag filtering, date range, and sorting by date or popularity (views). |
| `get_photo` | | `id`* | Return full metadata for a single photo by its Flickr ID. |
| `get_summary` | | | Total count, total views, date range, last sync time, and top 20 tags by frequency. |
| `list_recent_syncs` | | | Show sync history — when photo data was last fetched from Flickr. |
| `update_photo` | ✍️ | `id`*, `title`*, `description`*, `tags`* | Update title/description/tags on Flickr and locally. **All four fields are required on every call** — pass the unchanged value for any field not being updated. |
| `fetch_photo_image` | | `id`* | Download a photo and return it as an image for visual inspection (subject to the vision-gate setting in the Workbench chat). |
| `get_photo_comments` | | `photo_id`* | Fetch all comments on a photo. |
| `add_comment` | ✍️ | `photo_id`*, `comment_text`* | Post a comment on a photo. |
| `delete_comment` | ✍️ | `comment_id`* | Delete a comment. |
| `fave_photo` | ✍️ | `photo_id`* | Add a photo to your favorites. |
| `remove_fave` | ✍️ | `photo_id`* | Remove a photo from your favorites. |
| `get_photo_stats` | | `photo_id`*, `date` (default today) | View/favorite/comment stats for a specific date. |
| `find_weak_photos` | | *(optional `include_private`, etc.)* | Rank photos by a weakness score combining low views-per-day, zero favorites, and zero comments. Public-only by default. |
| `set_visibility` | ✍️ | `id`*, `is_public`*, `is_friend`, `is_family` | Make a photo public or private. |
| `set_location` | ✍️ | `id`*, `lat`*, `lon`*, `accuracy` (1–16, default 16) | Set geolocation. |
| `remove_location` | ✍️ | `id`* | Remove geolocation. |
| `set_safety_level` | ✍️ | `id`*, `safety_level`* | `safe` / `moderate` / `restricted`. |
| `set_content_type` | ✍️ | `id`*, `content_type`* | `photo` / `screenshot` / `other`. |
| `set_dates` | ✍️ | `id`*, `date_taken`*, `granularity` | Correct the date-taken timestamp (e.g. camera clock errors). |
| `get_exif` | | `photo_id`* | Camera, lens, exposure settings, etc. |
| `get_upload_status` | | | Upload bandwidth and storage status for the current month. |
| `get_person_info` | | `user_id`* (NSID or username) | Public profile info for any Flickr user. |
| `get_photostream_stats` | | | Total view counts across all photos, sets, and galleries for a given date. |
| `get_popular_photos` | | | Most popular photos sorted by favorites, comments, or views. |
| `get_photo_faves` | | `photo_id`*, `limit` (default 50) | Who favorited a photo, with a `you_follow` flag from the local contacts DB. |
| `get_faves` | | | Photos you have favorited. |
| `get_photos_with_comments` | | | Photos with ≥1 comment, most recently uploaded first (id/title/URL/count only — use `get_photo_comments` for the thread). |
| `get_recent_activity` | | | Recent comments and faves on your photos. |
| `add_to_keeper_list` | ✍️ | `photo_id`*, `note` | Flag a photo as worth preserving even if weak on stats. |
| `get_keeper_list` | | | List the keeper list. |
| `remove_from_keeper_list` | ✍️ | `photo_id`* | Remove a photo from the keeper list. |

## Albums (`tools/albums.py`) — 7 tools

| Tool | Write | Parameters | Description |
|---|:---:|---|---|
| `find_albums` | | *(optional keyword)* | Search your albums from the local database. |
| `get_album_photos` | | `album_id`*, `limit` (default 50), `page` (default 1) | List photos in an album. |
| `add_to_album` | ✍️ | `photo_id`*, `album_id`* | Add a photo to an album. |
| `remove_from_album` | ✍️ | `photo_id`*, `album_id`* | Remove a photo from an album. |
| `create_album` | ✍️ | `title`*, `primary_photo_id`*, `description` | Create a new album with an initial cover photo. |
| `edit_album` | ✍️ | `album_id`*, `title`, `description`, `primary_photo_id` | Rename, redescribe, or re-cover an album. |
| `delete_album` | ✍️ | `album_id`* | Delete an album (photos themselves are not deleted). |

## Groups (`tools/groups.py`) — 13 tools

| Tool | Write | Parameters | Description |
|---|:---:|---|---|
| `find_groups` | | *(optional keyword)* | Search your joined groups by name, description, or keywords. |
| `set_group_keywords` | ✍️ | `group_id`*, `keywords`* | Custom search synonyms for a group, to improve future `find_groups` matches. |
| `add_to_group` | ✍️ | `photo_id`*, `group_id`*, `retry_at`, `queue`, `days_offset` | Add a photo to a group pool. If the daily posting limit is hit, the add is queued for automatic retry; `queue=true` schedules a future add deliberately (drip-posting); `retry_at` accepts named times (`morning`, `lunchtime`, `afternoon`, `evening`, `night`, `midnight`) or `HH:MM`, resolved in Chicago time; `days_offset` shifts the schedule by N days. |
| `remove_from_group` | ✍️ | `photo_id`*, `group_id`* | Remove a photo from a group pool. |
| `join_group` | ✍️ | `group_id`* | Join a public group. |
| `leave_group` | ✍️ | `group_id`* | Leave a group. |
| `get_group_photos` | | `group_id`*, `limit` (default 50), `page` (default 1) | List photos in a group pool. |
| `search_all_groups` | | `query`*, `limit` (default 20) | Search all Flickr groups, not just ones you've joined. |
| `get_photo_contexts` | | `photo_id`*, `force_api` | All group pools and albums a photo currently belongs to — check before `add_to_group` to skip duplicates. |
| `get_group_stats` | | | How many of your photos are in each joined group, ranked by count. Requires a groups sync. |
| `get_photo_group_count` | | | Your photos ranked by how many groups they belong to — finds under/over-distributed photos. Requires a groups sync. |
| `get_group_queue` | | | Status of the pending group-add queue (waiting/success/error counts + details); also flushes anything whose retry window has passed. |
| `remove_from_queue` | ✍️ | `photo_id`*, `group_id`* | Remove a waiting item from the queue. |

## Contacts (`tools/contacts.py`) — 8 tools

| Tool | Write | Parameters | Description |
|---|:---:|---|---|
| `get_contacts_summary` | | | Total followed, friend/family breakdown, engagement stats, top engagers. |
| `find_unfollow_candidates` | | | Contacts ranked by lowest engagement (faves + comments on your photos); excludes the do-not-unfollow list. |
| `protect_contact` | ✍️ | `contact_id`*, `reason` | Add to the do-not-unfollow whitelist. |
| `follow_contact` | ✍️ | `contact_id`*, `is_friend`, `is_family` | Follow a user by NSID, optionally tagging friend/family. |
| `unfollow_contact` | ✍️ | `contact_id`* | Unfollow via the API (returns their profile URL regardless of outcome). |
| `get_contact_uploads` | | | Recent uploads from people you follow. |
| `find_follow_candidates` | | | People who faved/commented on your photos that you don't follow yet, ranked by engagement; excludes the never-follow list. |
| `add_to_never_follow` | ✍️ | `contact_id`*, `reason` | Permanently exclude a contact from follow suggestions. |

## Galleries (`tools/galleries.py`) — 4 tools

| Tool | Write | Parameters | Description |
|---|:---:|---|---|
| `get_galleries` | | | List galleries you've created (curated collections, distinct from albums). |
| `create_gallery` | ✍️ | `title`*, `description`*, `primary_photo_id` | Create a new gallery. |
| `add_to_gallery` | ✍️ | `gallery_id`*, `photo_id`*, `comment` | Add a photo to a gallery, with an optional annotation. |
| `get_gallery_photos` | | `gallery_id`*, `limit` (default 50), `page` (default 1) | List photos in a gallery. |

## Sync (`tools/sync.py`) — 1 tool

| Tool | Write | Parameters | Description |
|---|:---:|---|---|
| `sync` | ✍️ | `type` (`photos` default / `groups` / `contacts` / `albums` / `all` / `backfill`), `full` | Trigger a sync from within an MCP session. `full=true` re-fetches all photos instead of just updates; `type=backfill` walks the full upload history in date-range windows — the only way to reach accounts with more than ~4000 photos. |

*(`*` marks a required parameter.)*

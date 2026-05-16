Trigger all Flickr syncs via the MCP server's web UI and report results.

Use the `sync` MCP tool to run each sync type in order. Run them sequentially:

1. `sync` with type "photos" — incremental photo sync
2. `sync` with type "contacts" — sync contacts list
3. `sync` with type "groups" — sync group membership
4. `sync` with type "albums" — sync album list

After all complete, call `list_recent_syncs` and report how many items were synced in each category.

Note: engagement sync (faves/comments per contact) takes ~20 minutes and should only be triggered if the user explicitly asks for it.

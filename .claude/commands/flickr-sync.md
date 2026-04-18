Run all Flickr syncs in the correct order using background bash commands. Report progress as each completes.

Run in this order (each must complete before the next):

1. `bin/flickr-sync` — incremental photo sync (use --full only if the user asks)
2. `bin/sync-contacts` — sync contacts list
3. `bin/sync-groups` — sync group membership
4. `bin/sync-albums` — sync album list

After all complete, report how many items were synced in each category.

Note: `bin/sync-engagement` is intentionally excluded — it takes ~20 minutes and should be run manually when needed.

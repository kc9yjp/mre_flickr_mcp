Review people who fave or comment on your photos that you don't yet follow, and decide whether to follow, interact, or permanently exclude them.

1. Call `find_follow_candidates` (limit=20) — ranked by engagement (faves + comments on your photos), already excludes people you follow and anyone on the never-follow list.

2. If empty, say so and stop (either no engagement data — suggest running the engagement sync — or everyone active is already followed/excluded).

3. For each candidate, starting from the top:
   - Call `get_person_info` for username/realname.
   - Show: username, realname, faves + comments on your photos.
   - Open their profile in Safari: `osascript -e 'tell application "Safari" to set URL of current tab of front window to "https://www.flickr.com/photos/<nsid>/"'`
   - Recommend: follow (meaningful engagement) or interact first (fave/comment back on one of their photos before following).
   - Wait for the user to decide.

User decisions:
- **Follow**: call `follow_contact` (ask if they should also be marked as a friend)
- **Interact first**: help them find one of the candidate's recent photos to fave/comment on, then ask again about following
- **Never follow**: call `add_to_never_follow` with an optional reason — they'll never be suggested again
- **Skip**: move to the next candidate without action

After each decision, ask "next?" and continue.

Keep a running tally of new follows, friends marked, and never-follow additions, and report it at the end.

Review Flickr contacts as unfollow candidates one at a time.

1. Call `find_unfollow_candidates` (limit=20) to get the ranked list sorted by lowest engagement.
2. For each candidate (starting from the top):
   - Show: username, real name, faves on your photos, comments on your photos
   - Open their Flickr profile in Safari using osascript (navigate current tab)
   - Give a brief recommendation: unfollow (zero/low engagement) or protect (keep following)
   - Wait for the user to decide

User decisions:
- **Unfollow**: call `unfollow_contact` with open_browser=false (already open in Safari)
- **Protect**: call `protect_contact` with an optional reason, never suggest again
- **Skip**: move to next without action

After each decision, ask "next?" and move to the next candidate.

Keep a running count of unfollows and protections this session.

Note: If the API unfollow fails, the profile is already open in Safari for manual unfollowing.

Review recent comments on your photos and ensure each one has a reply.

1. Call `get_unreplied_comments` (timeframe="7d") to get photos with comments that don't have a reply yet. Each result includes photo id, title, url_photopage, and the list of unreplied comments (author, author_nsid, author_url, date, comment, permalink). Note: it only looks at activity within the given timeframe, so older unreplied comments outside that window won't show up — widen the timeframe (max "7d") if you suspect there's a backlog.

2. Present the unreplied comments one at a time:
   - Show: photo title, commenter username, comment text
   - Open the photo in the browser using AppleScript: `osascript -e 'tell application "Safari" to set URL of current tab of front window to "<photo_url>"'`
   - Suggest at least 5 short reply options with variety (emoji-only, brief thanks, specific acknowledgment, warm, casual). Each suggestion must be formatted as a Flickr reply: `[<author_url>] <message>` using the `author_url` field from `get_unreplied_comments`.
   - Wait for user to pick a number, type custom text, or skip

3. Once confirmed, post the reply with `add_comment`. The comment must start with `[<author_url>]` to notify the commenter.

4. After each reply, ask "next?" and move to the next unreplied comment.

Keep a running count of replies posted this session.

Note: Never include self-promotional URLs in comments.

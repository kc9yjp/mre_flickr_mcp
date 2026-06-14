Review recent comments on your photos and ensure each one has a reply.

1. Call `get_recent_activity` with `timeframe="week"` to get recent activity.

2. Filter to only `type="comment"` events. Collect the unique photo IDs that have comments.

3. For each photo with comments, call `get_photo_comments` to get the full comment thread.

4. For each comment thread, check whether you (ejwettstein / nsid 45293338@N00) have already replied. A reply counts if any comment in the thread by you appears *after* the commenter's comment.

5. For comments that have **no reply yet**, present them one at a time:
   - Show: photo title, commenter username, comment text
   - Open the photo in the browser using the standard browser-open method for this machine
   - Suggest at least 5 short reply options with variety (emoji-only, brief thanks, specific acknowledgment, warm, casual). Always prefix each suggestion with `@<commenter_username>` so they are notified of the reply.
   - Wait for user to pick a number, type custom text, or skip

6. Once confirmed, post the reply with `add_comment`. The comment must include `@<commenter_username>` at the start.

7. After each reply, ask "next?" and move to the next unreplied comment.

Keep a running count of replies posted this session.

Note: Never include self-promotional URLs in comments.

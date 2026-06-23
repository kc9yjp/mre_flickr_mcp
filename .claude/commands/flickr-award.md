Post a group award comment to photos from the currently open Flickr group in Safari.

## Step 1 — Get the group and award template

1. Get the current Safari URL using AppleScript:
   `osascript -e 'tell application "Safari" to get URL of current tab of front window'`

2. Extract the group ID from the URL (format: `flickr.com/groups/[group-id]`).

3. Fetch the group's discuss page to find the award template:
   `WebFetch("https://www.flickr.com/groups/[group-id]/discuss/", "Find a discussion thread about awards. Return the exact HTML of the award template — it typically contains an <img> tag linking to a photo and text like 'seen and admired in [GROUP NAME]'.")`

   If the template can't be extracted automatically, ask the user to paste it.

4. Show the user the award template and confirm it looks right before proceeding.

## Step 2 — Apply awards

5. Tell the user: "Navigate to a photo in Safari and say 'apply' to post the award."

6. When the user says "apply" (or "next", or a photo URL/ID):
   - Get the current Safari URL and extract the photo ID
   - Post the award template text as a comment using `add_comment`
   - Confirm: "Award posted on [photo title or ID]."

7. Ask "Next?" and repeat from step 6.

Keep a running count of awards posted this session.

Note: Never modify the award template text — post it exactly as-is.

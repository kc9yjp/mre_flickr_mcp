Get the URL of the current Safari tab using osascript. Extract the Flickr photo ID from the URL.

Then do the following in order:

1. Fetch the photo image using the `fetch_photo_image` MCP tool so you can see it visually.

2. Look at the photo carefully and suggest:
   - A concise, descriptive title
   - A 1-2 sentence description that captures the mood, subject, and context
   - A set of relevant tags (location, subject, style, equipment if apparent)
   Ask the user to confirm, adjust, or skip before applying.

3. Once confirmed, update the metadata using `update_photo` and make it public using `set_visibility` if it isn't already.

4. Search the user's groups using `find_groups` with 2-3 relevant keyword searches based on the photo's subject and location. Suggest the top 2-3 most relevant groups and ask the user to confirm before adding with `add_to_group`.

5. Refresh the Safari tab by re-setting its URL to the photo page.

Keep suggestions concise. Ask for confirmation at each step before taking action. Remember the user has an artsy bent and shoots in Chicago/Oak Park — factor in location context when suggesting tags and groups.

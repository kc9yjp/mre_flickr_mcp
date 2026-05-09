Get the URL of the current Safari tab using osascript. Extract the Flickr photo ID from the URL.

Then do the following in order:

1. Fetch the photo image using the `fetch_photo_image` MCP tool so you can see it visually.

2. Fave it immediately using `fave_photo` — no confirmation needed.

3. Look at the photo carefully and suggest 3 short comment options. Keep them artsy, specific, and genuine — reference something concrete in the image (light, composition, subject, mood). Avoid generic praise. Let the user pick a number, tweak, or skip.

4. Once the user picks, post it with `add_comment`.

Keep it fast — fave first, then suggest comments.

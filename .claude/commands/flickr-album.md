Get the URL of the current Safari tab using osascript. Extract the Flickr photo ID from the URL.

1. Fetch the photo image using `fetch_photo_image` so you can see it.
2. Search the user's albums using `find_albums` with 2-3 relevant keywords based on what you see in the photo (subject, location, style, season).
3. Suggest the best 1-3 matching albums with a brief reason for each.
4. Wait for the user to confirm which album(s) to add to.
5. Add the photo using `add_to_album` for each confirmed album.
6. Refresh the Safari tab by re-setting its URL.

If no good match exists in current albums, offer to create a new one with `create_album` using the current photo as the primary/cover image.

Remember the user's albums include themed collections (Flowers, Tulips, Roses, Dahlias, Chicago or There About, Daily Walks, Hipstamatic, Macros, Lady Jane, Sunrise & Sunset, etc.) — use this context when suggesting.

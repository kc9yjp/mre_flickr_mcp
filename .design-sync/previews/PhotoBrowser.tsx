import { PhotoBrowser } from "flickr-workbench";
import { screen } from "./_fixtures";

// /api/photos for the default "My Photos" tab, /api/albums for "My Albums".
// Thumbnails are inline SVG data URIs — previews render from file:// with no
// network, so any real Flickr thumbnail URL would come up broken.
screen();

export function Default() {
  return <PhotoBrowser />;
}

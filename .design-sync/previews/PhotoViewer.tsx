import { PhotoViewer } from "flickr-workbench";
import { screen, PHOTO_DETAIL } from "./_fixtures";

// PhotoViewer opens whichever photo the bus last emitted, falling back to the
// URL hash (#photo=<id>) — its own deep-link path, used by the bookmarklet and
// browser extension. Setting the hash before mount is the honest way to give
// the panel a photo without reaching into the bus.
window.location.hash = `photo=${PHOTO_DETAIL.id}`;
screen();

export function Default() {
  return <PhotoViewer />;
}

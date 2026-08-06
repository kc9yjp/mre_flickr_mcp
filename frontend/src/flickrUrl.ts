// Client-side classifier for a single Flickr URL dropped into chat — mirrors
// the bookmarklet/extension's own regex-based routing (see the bookmarklet
// JS built in api_setup and the popup.js built in api_extension, both in
// scripts/webapi.py) but extended beyond photo pages to also recognize
// user/profile, group, and album pages.

export type FlickrRoute =
  | { kind: "photo"; id: string }
  | { kind: "album"; owner: string; albumId: string }
  | { kind: "group"; url: string }
  | { kind: "user"; ref: string };

// Album must be checked before the plainer photo pattern, and user must be
// checked last — it's the broadest pattern (bare owner segment, nothing
// after) and would otherwise shadow the more specific ones.
const ALBUM_RE = /flickr\.com\/photos\/([^/\s?#]+)\/(?:albums|sets)\/(\d+)/i;
const PHOTO_RE = /flickr\.com\/photos\/[^/\s?#]+\/(\d+)/i;
const GROUP_RE = /flickr\.com\/groups\/[^/\s?#]+/i;
const USER_RE = /flickr\.com\/(?:photos|people)\/([^/\s?#]+)\/?(?:[?#]|$)/i;

// flic.kr/p/<code> is Flickr's short-link format — what the official mobile
// app's share sheet produces — where <code> is the numeric photo id encoded
// in a base58-ish alphabet (digits/letters minus the visually ambiguous
// 0/O/I/l). Left undecoded, that code looks enough like an id that a naive
// caller (including the LLM, if this routing is skipped) can mistake it for
// one and pass it straight to the Flickr API, which just 404s.
const SHORT_URL_RE = /flic\.kr\/p\/([0-9a-zA-Z]+)/i;
const SHORT_ALPHABET = "123456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ";

function decodeShortId(code: string): string | null {
  let n = 0;
  for (const ch of code) {
    const idx = SHORT_ALPHABET.indexOf(ch);
    if (idx === -1) return null; // not valid in Flickr's short-id alphabet
    n = n * SHORT_ALPHABET.length + idx;
  }
  return n > 0 ? String(n) : null;
}

/** Classify a chat message that is *only* a Flickr URL (nothing else in the
 * message) into one of the page types the Workbench can jump straight to a
 * panel for. Returns null for anything else — not a URL, not a Flickr URL,
 * or a Flickr URL of a kind we don't have a dedicated view for (e.g. a
 * gallery) — so the caller falls back to sending it through the normal chat
 * loop instead. */
export function classifyFlickrUrl(text: string): FlickrRoute | null {
  if (!/^https?:\/\/\S+$/i.test(text) || !/flickr\.com|flic\.kr/i.test(text)) return null;

  let m = text.match(SHORT_URL_RE);
  if (m) {
    const id = decodeShortId(m[1]);
    if (id) return { kind: "photo", id };
    // Not a valid short code (or too short to be one) — fall through in case
    // it's some other flic.kr path we don't otherwise recognize.
  }

  m = text.match(ALBUM_RE);
  if (m) return { kind: "album", owner: decodeURIComponent(m[1]), albumId: m[2] };

  m = text.match(PHOTO_RE);
  if (m) return { kind: "photo", id: m[1] };

  if (GROUP_RE.test(text)) return { kind: "group", url: text };

  m = text.match(USER_RE);
  if (m) return { kind: "user", ref: decodeURIComponent(m[1]) };

  return null;
}

// Flickr descriptions/bios/group rules are HTML, not plain text or Markdown
// (Flickr's own editor lets users bold/italicize/link) — rendering the raw
// string as a React text child just prints the tags literally. This renders
// a small allowlisted subset instead of the raw string.
//
// Never dangerouslySetInnerHTML: DOMParser output here is only ever *read*
// (walked and rebuilt into React elements below), never reattached to the
// live document — so even if a description contains a <script> or an
// onerror= handler, nothing executes and nothing gets to run through
// React's own escaping.

import { Fragment, type ReactNode } from "react";

const ALLOWED_TAGS = new Set([
  "b", "strong", "i", "em", "u", "br", "p", "blockquote",
  "ul", "ol", "li", "span", "div",
]);

function sanitizeHref(href: string | null): string | null {
  if (!href) return null;
  try {
    const url = new URL(href, window.location.href);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function domToReact(node: ChildNode, key: string): ReactNode {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return null;

  const el = node as Element;
  const tag = el.tagName.toLowerCase();
  if (tag === "br") return <br key={key} />;

  const children = Array.from(el.childNodes).map((child, i) => (
    <Fragment key={`${key}-${i}`}>{domToReact(child, `${key}-${i}`)}</Fragment>
  ));

  if (tag === "a") {
    const href = sanitizeHref(el.getAttribute("href"));
    return href ? (
      <a key={key} href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    ) : (
      <Fragment key={key}>{children}</Fragment>
    );
  }

  if (!ALLOWED_TAGS.has(tag)) return <Fragment key={key}>{children}</Fragment>;

  const Tag = tag as "b" | "strong" | "i" | "em" | "u" | "p" | "blockquote" | "ul" | "ol" | "li" | "span" | "div";
  return <Tag key={key}>{children}</Tag>;
}

/** Render a Flickr free-text field (description/bio/group rules) as safe,
 * limited HTML instead of an escaped literal string. */
export function SafeHtml({ html, className }: { html: string; className?: string }): JSX.Element {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const nodes = Array.from(doc.body.childNodes).map((n, i) => (
    <Fragment key={i}>{domToReact(n, `n${i}`)}</Fragment>
  ));
  return <span className={className} style={{ whiteSpace: "pre-wrap" }}>{nodes}</span>;
}

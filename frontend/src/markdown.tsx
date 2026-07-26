// Minimal, dependency-free Markdown renderer for chat messages. Assistant
// replies (esp. tables and lists from LLM tool summaries) were showing up as
// raw "| a | b |" text — this covers the subset of GFM that actually shows up
// in practice (paragraphs, headings, lists, tables, code blocks, blockquotes,
// bold/italic/code/links) without pulling in a markdown dependency.

import { Fragment, type ReactNode } from "react";

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|");
}

function isTableSeparator(line: string): boolean {
  const cells = splitRow(line);
  return cells.length > 0 && cells.every((c) => /^:?-+:?$/.test(c.trim()));
}

function parseInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*]+)\*|_([^_]+)_|\[([^\]]+)\]\(([^)]+)\)/g;
  const out: ReactNode[] = [];
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > lastIndex) out.push(text.slice(lastIndex, m.index));
    const key = `${keyPrefix}-${i++}`;
    if (m[1] !== undefined) out.push(<code key={key}>{m[1]}</code>);
    else if (m[2] !== undefined) out.push(<strong key={key}>{m[2]}</strong>);
    else if (m[3] !== undefined) out.push(<strong key={key}>{m[3]}</strong>);
    else if (m[4] !== undefined) out.push(<em key={key}>{m[4]}</em>);
    else if (m[5] !== undefined) out.push(<em key={key}>{m[5]}</em>);
    else if (m[6] !== undefined) out.push(
      <a key={key} href={m[7]} target="_blank" rel="noopener noreferrer">{m[6]}</a>,
    );
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return out;
}

export function Markdown({ text }: { text: string }): JSX.Element {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  const isBlockStart = (idx: number): boolean => {
    const line = lines[idx];
    if (line.trim() === "") return true;
    if (line.trim().startsWith("```")) return true;
    if (/^(#{1,6})\s+/.test(line)) return true;
    if (/^\s*[-*+]\s+/.test(line)) return true;
    if (/^\s*\d+[.)]\s+/.test(line)) return true;
    if (line.trim().startsWith(">")) return true;
    if (line.includes("|") && idx + 1 < lines.length && isTableSeparator(lines[idx + 1])) return true;
    return false;
  };

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") {
      i++;
      continue;
    }

    if (line.trim().startsWith("```")) {
      i++;
      const codeLines: string[] = [];
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++;
      blocks.push(
        <pre className="md-code-block" key={key++}>
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      blocks.push(
        <div className={`md-heading md-h${level}`} key={key}>
          {parseInline(headingMatch[2], `hd${key++}`)}
        </div>,
      );
      i++;
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const header = splitRow(line).map((c) => c.trim());
      const tableKey = key++;
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(splitRow(lines[i]).map((c) => c.trim()));
        i++;
      }
      blocks.push(
        <div className="md-table-wrap" key={tableKey}>
          <table className="md-table">
            <thead>
              <tr>{header.map((h, hi) => <th key={hi}>{parseInline(h, `t${tableKey}h${hi}`)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>{r.map((c, ci) => <td key={ci}>{parseInline(c, `t${tableKey}r${ri}c${ci}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (line.trim().startsWith(">")) {
      const quoteKey = key++;
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote className="md-quote" key={quoteKey}>
          {parseInline(quoteLines.join(" "), `bq${quoteKey}`)}
        </blockquote>,
      );
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const listKey = key++;
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      blocks.push(
        <ul className="md-list" key={listKey}>
          {items.map((it, ii) => <li key={ii}>{parseInline(it, `ul${listKey}-${ii}`)}</li>)}
        </ul>,
      );
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const listKey = key++;
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i++;
      }
      blocks.push(
        <ol className="md-list" key={listKey}>
          {items.map((it, ii) => <li key={ii}>{parseInline(it, `ol${listKey}-${ii}`)}</li>)}
        </ol>,
      );
      continue;
    }

    const paraKey = key++;
    const paraLines: string[] = [];
    while (i < lines.length && !isBlockStart(i)) {
      paraLines.push(lines[i]);
      i++;
    }
    blocks.push(
      <p className="md-paragraph" key={paraKey}>
        {paraLines.map((l, li) => (
          <Fragment key={li}>
            {li > 0 && <br />}
            {parseInline(l, `p${paraKey}-${li}`)}
          </Fragment>
        ))}
      </p>,
    );
  }

  return <>{blocks}</>;
}

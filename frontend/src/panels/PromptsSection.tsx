// Editor for user-defined and built-in prompts and the template variables
// ({photo_id}, {user_nsid}) they can reference. Categories are fixed by the
// system (not user-editable) — each one pins its prompts' workflow buttons
// to a specific page, shown here as a badge on the category's group header
// rather than a picker. Prompts within a group are accordion cards (mirrors
// the connection/model cards in Models): click a row to expand it into
// an editor, click again (or Save/Reset) to collapse it.

import { FormEvent, useEffect, useRef, useState } from "react";
import { Prompt, PromptCategory, PromptVariable, PromptsData, getJSON, postJSON } from "../api";
import * as bus from "../bus";

// Where a category's prompts show up as workflow buttons — mirrors the
// server-side category → context mapping in agent/prompts_store.py.
const CATEGORY_PLACEMENT: Record<string, string> = {
  system: "internal only — no button",
  own_photo: "Photo Viewer",
  other_photo: "Photo Viewer",
  collection: "Chat / Command Palette",
};

function placementFor(categoryId: string): string {
  return CATEGORY_PLACEMENT[categoryId] ?? "Chat / Command Palette";
}

function contextLabel(context: Prompt["context"]): string {
  return context === "photo" ? "Photo" : "Global";
}

interface ParsedPrompt {
  categoryName: string;
  name: string;
  code: string;
  context: string;
  description: string;
  text: string;
}

interface ParsedExport {
  categories: { name: string; description: string }[];
  prompts: ParsedPrompt[];
}

// Mirrors exportMarkdown's layout: "## Category", "### Prompt", a
// "*Code: `code` — Context: context*" line, an optional description, then
// the prompt text in a fenced code block.
function parseExportedMarkdown(content: string): ParsedExport {
  const lines = content.split("\n");
  const categories: { name: string; description: string }[] = [];
  const prompts: ParsedPrompt[] = [];
  const metaRe = /^\*Code: `([^`]+)` — Context: (\w+)\*$/;

  let i = 0;
  let currentCategory: string | null = null;
  let categoryDescLines: string[] = [];
  const flushCategory = () => {
    if (currentCategory !== null) {
      categories.push({ name: currentCategory, description: categoryDescLines.join("\n").trim() });
    }
  };

  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("## ")) {
      flushCategory();
      currentCategory = line.slice(3).trim();
      categoryDescLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("### ") && !lines[i].startsWith("## ")) {
        categoryDescLines.push(lines[i]);
        i++;
      }
      continue;
    }
    if (line.startsWith("### ") && currentCategory !== null) {
      const name = line.slice(4).trim();
      i++;
      let code = "";
      let context = "global";
      const meta = lines[i]?.match(metaRe);
      if (meta) {
        code = meta[1];
        context = meta[2];
        i++;
      }
      const descLines: string[] = [];
      while (i < lines.length && lines[i].trim() !== "```") {
        descLines.push(lines[i]);
        i++;
      }
      if (lines[i]?.trim() === "```") i++;
      const textLines: string[] = [];
      while (i < lines.length && lines[i].trim() !== "```") {
        textLines.push(lines[i]);
        i++;
      }
      if (lines[i]?.trim() === "```") i++;
      if (code) {
        prompts.push({
          categoryName: currentCategory,
          name,
          code,
          context,
          description: descLines.join("\n").trim(),
          text: textLines.join("\n").trim(),
        });
      }
      continue;
    }
    i++;
  }
  flushCategory();
  return { categories, prompts };
}

interface PromptDraft {
  name: string;
  description: string;
  text: string;
}

export function PromptsSection() {
  const [data, setData] = useState<PromptsData | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [drafts, setDrafts] = useState<Record<string, PromptDraft>>({});
  const [pendingEditId, setPendingEditId] = useState<string | null>(null);
  const [showAddPrompt, setShowAddPrompt] = useState(false);
  const [newPrompt, setNewPrompt] = useState({
    code: "", name: "", description: "", category_id: "", text: "",
  });
  const [newVariable, setNewVariable] = useState({ code: "", label: "", description: "" });
  const importInputRef = useRef<HTMLInputElement>(null);

  const load = () =>
    getJSON<PromptsData>("/api/prompts")
      .then((d) => {
        setData(d);
        setNewPrompt((p) => ({ ...p, category_id: p.category_id || d.categories[0]?.id || "" }));
        // Tell Command/PhotoBrowser/CommandPalette to refetch /api/commands —
        // otherwise their workflow buttons keep sending stale prompt text.
        bus.emit("promptsChanged", undefined);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));

  useEffect(() => { load(); }, []);

  const flash = (msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(""), 3000);
  };

  // Opens a prompt's card, (re)seeding its draft from the saved values —
  // any unsaved edits from a previous expand are discarded, matching a
  // plain accordion (there's no separate Cancel for an open card; closing
  // it and reopening resets the draft).
  const openPrompt = (p: Prompt) => {
    setDrafts((d) => ({ ...d, [p.id]: { name: p.name, description: p.description, text: p.text } }));
    setExpandedIds((s) => new Set(s).add(p.id));
  };

  const toggleExpand = (p: Prompt) => {
    if (expandedIds.has(p.id)) {
      setExpandedIds((s) => {
        const next = new Set(s);
        next.delete(p.id);
        return next;
      });
    } else {
      openPrompt(p);
    }
  };

  const collapse = (id: string) => {
    setExpandedIds((s) => {
      const next = new Set(s);
      next.delete(id);
      return next;
    });
  };

  const setDraftField = (id: string, patch: Partial<PromptDraft>) => {
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  };

  // A message sent from a workflow prompt (in Chat) can request this panel
  // jump straight to editing that prompt. The bus event alone can arrive
  // before this panel has mounted/subscribed, so also check the sticky slot
  // once on mount.
  useEffect(() => {
    const sticky = bus.consumePendingEditPrompt();
    if (sticky) setPendingEditId(sticky);
    return bus.on("editPrompt", setPendingEditId);
  }, []);

  useEffect(() => {
    if (!pendingEditId || !data) return;
    const p = data.prompts.find((x) => x.id === pendingEditId);
    if (p) openPrompt(p);
    setPendingEditId(null);
  }, [pendingEditId, data]);

  const saveEdit = async (id: string) => {
    const draft = drafts[id];
    if (!draft) return;
    try {
      await postJSON(`/api/prompts/${id}`, draft);
      collapse(id);
      flash("Saved.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const resetPrompt = async (id: string) => {
    try {
      await postJSON(`/api/prompts/${id}/reset`, {});
      collapse(id);
      flash("Reset to default.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const resetAllPrompts = async () => {
    if (!window.confirm(
      "Reset every built-in prompt to its shipped default? Any edits you've made to them will be lost. " +
      "Prompts you've added yourself are not affected."
    )) return;
    const includeUserMemory = window.confirm(
      "Also clear your Standing Memory (the guidance the assistant has accumulated via \"remember\")? " +
      "Choose Cancel to leave Standing Memory as it is."
    );
    try {
      await postJSON("/api/prompts/reset-all", { include_user_memory: includeUserMemory });
      flash(includeUserMemory
        ? "All built-in prompts reset, including Standing Memory."
        : "All built-in prompts reset (Standing Memory kept).");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const deletePrompt = async (id: string) => {
    try {
      await postJSON(`/api/prompts/${id}/delete`, {});
      collapse(id);
      flash("Deleted.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const createPrompt = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await postJSON("/api/prompts", newPrompt);
      setNewPrompt((p) => ({ ...p, code: "", name: "", description: "", text: "" }));
      setShowAddPrompt(false);
      flash("Prompt added.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const createVariable = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await postJSON("/api/prompt-variables", newVariable);
      setNewVariable({ code: "", label: "", description: "" });
      flash("Variable added.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const deleteVariable = async (code: string) => {
    try {
      await postJSON(`/api/prompt-variables/${code}/delete`, {});
      flash("Variable deleted.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const copyVar = (code: string) => {
    navigator.clipboard?.writeText(`{${code}}`).then(() => flash(`Copied {${code}}`));
  };

  const exportMarkdown = () => {
    if (!data) return;
    const lines: string[] = ["# Prompts Export", ""];
    for (const cat of data.categories) {
      const prompts = data.prompts.filter((p) => p.category_id === cat.id);
      if (prompts.length === 0) continue;
      lines.push(`## ${cat.name}`);
      if (cat.description) lines.push("", cat.description);
      lines.push("");
      for (const p of prompts) {
        lines.push(`### ${p.name}`);
        lines.push(`*Code: \`${p.code}\` — Context: ${p.context}*`);
        if (p.description) lines.push("", p.description);
        lines.push("", "```", p.text, "```", "");
      }
    }
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `prompts-export-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);
    flash("Exported.");
  };

  const importMarkdown = async (file: File) => {
    if (!data) return;
    try {
      // Categories are fixed by the system, not created from an import —
      // prompts whose exported category name doesn't match an existing one
      // are skipped rather than inventing a new category for them.
      const parsed = parseExportedMarkdown(await file.text());
      const categoriesByName = new Map(data.categories.map((c) => [c.name, c]));
      const promptsByCode = new Map(data.prompts.map((p) => [p.code, p]));
      let created = 0;
      let updated = 0;
      let skipped = 0;
      for (const p of parsed.prompts) {
        const category = categoriesByName.get(p.categoryName);
        if (!category) {
          skipped++;
          continue;
        }
        const existing = promptsByCode.get(p.code);
        if (existing) {
          await postJSON(`/api/prompts/${existing.id}`, {
            name: p.name, description: p.description, category_id: category.id, text: p.text,
          });
          updated++;
        } else {
          await postJSON("/api/prompts", {
            code: p.code, name: p.name, description: p.description,
            category_id: category.id, text: p.text,
          });
          created++;
        }
      }
      flash(`Imported: ${created} created, ${updated} updated${skipped ? `, ${skipped} skipped (unknown category)` : ""}.`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (!data) return <div>{error || "Loading…"}</div>;

  return (
    <div>
      <p className="hint">
        Built-in and custom prompts the assistant can use, grouped by where they show up. Categories are
        fixed by the system — add, edit, or reset individual prompts below.
      </p>
      {status && <p className="hint">{status}</p>}
      {error && <p className="error">{error}</p>}

      <div className="settings-row" style={{ marginBottom: 20 }}>
        <button onClick={exportMarkdown}>Export as Markdown</button>
        <button onClick={() => importInputRef.current?.click()}>Import from Markdown</button>
        <button className="btn-danger-sm" onClick={resetAllPrompts}>Reset all to default</button>
        <input
          ref={importInputRef}
          type="file"
          accept=".md,text/markdown"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) importMarkdown(file);
          }}
        />
      </div>

      {data.categories.map((cat: PromptCategory) => {
        const prompts = data.prompts.filter((p) => p.category_id === cat.id);
        if (prompts.length === 0) return null;
        return (
          <div key={cat.id} className="prompt-group">
            <div>
              <div className="prompt-group-title-row">
                <span className="prompt-group-name">{cat.name}</span>
                <span className="prompt-group-placement">→ {placementFor(cat.id)}</span>
              </div>
              {cat.description && <div className="prompt-group-desc">{cat.description}</div>}
            </div>

            <div className="prompt-list">
              {prompts.map((p) => {
                const expanded = expandedIds.has(p.id);
                const draft = drafts[p.id];
                return (
                  <div key={p.id} className="llm-model-card">
                    <button
                      type="button"
                      className="llm-conn-header-main"
                      onClick={() => toggleExpand(p)}
                    >
                      <div className="llm-conn-title">
                        <div className="llm-conn-name-row">
                          <span className="llm-conn-name">{p.name}</span>
                          <span className="prompt-card-code">{p.code}</span>
                          <span className="llm-kind-badge">{contextLabel(p.context)}</span>
                        </div>
                        <div className="prompt-card-desc">{p.description}</div>
                      </div>
                      <span className={`llm-chevron${expanded ? " expanded" : ""}`}>›</span>
                    </button>

                    {expanded && draft && (
                      <div className="llm-model-body">
                        <div className="llm-field-grid">
                          <label className="llm-field">
                            Display name
                            <input
                              value={draft.name}
                              onChange={(e) => setDraftField(p.id, { name: e.target.value })}
                            />
                          </label>
                          <label className="llm-field">
                            Description
                            <input
                              value={draft.description}
                              onChange={(e) => setDraftField(p.id, { description: e.target.value })}
                            />
                          </label>
                        </div>
                        <label className="llm-field">
                          Prompt text
                          <textarea
                            className="prompt-text"
                            rows={6}
                            placeholder="Markdown supported — use {photo_id} / {user_nsid} where needed"
                            value={draft.text}
                            onChange={(e) => setDraftField(p.id, { text: e.target.value })}
                          />
                        </label>
                        <div className="llm-row-end">
                          {p.builtin ? (
                            <button type="button" className="btn-sm" onClick={() => resetPrompt(p.id)}>
                              Reset to default
                            </button>
                          ) : (
                            <button type="button" className="btn-danger-sm" onClick={() => deletePrompt(p.id)}>
                              Delete
                            </button>
                          )}
                          <button type="button" className="btn-sm btn-filled" onClick={() => saveEdit(p.id)}>
                            Save
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {!showAddPrompt ? (
        <button type="button" className="llm-add-custom-btn" onClick={() => setShowAddPrompt(true)}>
          + Add prompt
        </button>
      ) : (
        <form onSubmit={createPrompt} className="llm-conn-card llm-add-card">
          <div className="llm-field-grid">
            <label className="llm-field">
              Code (unique)
              <input
                placeholder="e.g. suggest-tags"
                value={newPrompt.code}
                onChange={(e) => setNewPrompt((p) => ({ ...p, code: e.target.value }))}
              />
            </label>
            <label className="llm-field">
              Display name
              <input
                value={newPrompt.name}
                onChange={(e) => setNewPrompt((p) => ({ ...p, name: e.target.value }))}
              />
            </label>
            <label className="llm-field">
              Category
              <select
                value={newPrompt.category_id}
                onChange={(e) => setNewPrompt((p) => ({ ...p, category_id: e.target.value }))}
              >
                {data.categories.map((c) => <option key={c.id} value={c.id}>{c.name} — {placementFor(c.id)}</option>)}
              </select>
            </label>
          </div>
          <label className="llm-field">
            Description
            <input
              value={newPrompt.description}
              onChange={(e) => setNewPrompt((p) => ({ ...p, description: e.target.value }))}
            />
          </label>
          <label className="llm-field">
            Prompt text
            <textarea
              className="prompt-text"
              rows={5}
              placeholder="Markdown supported — use {photo_id} / {user_nsid} where needed"
              value={newPrompt.text}
              onChange={(e) => setNewPrompt((p) => ({ ...p, text: e.target.value }))}
            />
          </label>
          <div className="llm-row-end">
            <button type="button" onClick={() => setShowAddPrompt(false)}>Cancel</button>
            <button
              type="submit"
              className="btn-filled"
              disabled={!newPrompt.code.trim() || !newPrompt.name.trim() || !newPrompt.text.trim()}
            >
              Save prompt
            </button>
          </div>
        </form>
      )}

      <div className="llm-active-card" style={{ marginTop: 24 }}>
        <div>
          <div className="llm-section-label">Substitution variables</div>
          <p className="hint" style={{ margin: "4px 0 0" }}>
            Reference for writing prompts — click one to copy it, then paste it into a prompt's text. Only{" "}
            <code>{"{photo_id}"}</code> and <code>{"{user_nsid}"}</code> are actually substituted by the
            backend; the rest are documentation only.
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {data.variables.map((v: PromptVariable) => (
            <div key={v.code} className="prompt-var-row">
              <button type="button" className="prompt-var-token" onClick={() => copyVar(v.code)}>
                {"{" + v.code + "}"}
              </button>
              <strong>{v.label}</strong>
              <span className="prompt-var-desc">{v.description}</span>
              {!v.builtin && (
                <button className="btn-danger-sm" onClick={() => deleteVariable(v.code)}>Delete</button>
              )}
            </div>
          ))}
          <form onSubmit={createVariable} className="settings-row">
            <input
              placeholder="code (e.g. group_id)"
              value={newVariable.code}
              onChange={(e) => setNewVariable((v) => ({ ...v, code: e.target.value }))}
            />
            <input
              placeholder="Label"
              value={newVariable.label}
              onChange={(e) => setNewVariable((v) => ({ ...v, label: e.target.value }))}
            />
            <input
              placeholder="Description"
              value={newVariable.description}
              onChange={(e) => setNewVariable((v) => ({ ...v, description: e.target.value }))}
            />
            <button type="submit" disabled={!newVariable.code.trim() || !newVariable.label.trim()}>
              Add variable
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

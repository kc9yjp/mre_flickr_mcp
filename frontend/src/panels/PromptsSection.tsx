// Editor for user-defined and built-in prompts, their categories, and the
// template variables ({photo_id}, {user_nsid}) they can reference.

import { FormEvent, useEffect, useState } from "react";
import { Prompt, PromptCategory, PromptVariable, PromptsData, getJSON, postJSON } from "../api";
import * as bus from "../bus";

const CONTEXTS: { value: Prompt["context"]; label: string }[] = [
  { value: "global", label: "global (collection-wide)" },
  { value: "photo", label: "photo (needs a selected photo)" },
];

export function PromptsSection() {
  const [data, setData] = useState<PromptsData | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Partial<Prompt>>({});
  const [pendingEditId, setPendingEditId] = useState<string | null>(null);
  const [showAddPrompt, setShowAddPrompt] = useState(false);
  const [newPrompt, setNewPrompt] = useState({
    code: "", name: "", description: "", category_id: "", context: "global", text: "",
  });
  const [newCategory, setNewCategory] = useState({ name: "", description: "" });
  const [newVariable, setNewVariable] = useState({ code: "", label: "", description: "" });

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

  const startEdit = (p: Prompt) => {
    setEditingId(p.id);
    setDraft({ name: p.name, description: p.description, category_id: p.category_id, context: p.context, text: p.text });
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
    if (p) startEdit(p);
    setPendingEditId(null);
  }, [pendingEditId, data]);

  const saveEdit = async (id: string) => {
    try {
      await postJSON(`/api/prompts/${id}`, draft);
      setEditingId(null);
      flash("Saved.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const resetPrompt = async (id: string) => {
    try {
      await postJSON(`/api/prompts/${id}/reset`, {});
      flash("Reset to default.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const deletePrompt = async (id: string) => {
    try {
      await postJSON(`/api/prompts/${id}/delete`, {});
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

  const createCategory = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await postJSON("/api/prompt-categories", newCategory);
      setNewCategory({ name: "", description: "" });
      flash("Category added.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const deleteCategory = async (id: string) => {
    try {
      await postJSON(`/api/prompt-categories/${id}/delete`, {});
      flash("Category deleted.");
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

  if (!data) return <div>{error || "Loading…"}</div>;

  return (
    <div>
      {status && <p className="hint">{status}</p>}
      {error && <p className="error">{error}</p>}

      <h3>Categories</h3>
      <div className="settings-item">
        {data.categories.map((c: PromptCategory) => (
          <div key={c.id} className="settings-row">
            <strong>{c.name}</strong>
            <span className="hint">{c.description}</span>
            {c.builtin ? (
              <span className="hint">built-in</span>
            ) : (
              <button className="btn-danger-sm" onClick={() => deleteCategory(c.id)}>Delete</button>
            )}
          </div>
        ))}
        <form onSubmit={createCategory} className="settings-row">
          <input
            placeholder="New category name"
            value={newCategory.name}
            onChange={(e) => setNewCategory((c) => ({ ...c, name: e.target.value }))}
          />
          <input
            placeholder="Description"
            value={newCategory.description}
            onChange={(e) => setNewCategory((c) => ({ ...c, description: e.target.value }))}
          />
          <button type="submit" disabled={!newCategory.name.trim()}>Add category</button>
        </form>
      </div>

      <h3>Prompts</h3>
      {data.categories.map((cat) => {
        const prompts = data.prompts.filter((p) => p.category_id === cat.id);
        if (prompts.length === 0) return null;
        return (
          <div key={cat.id} className="settings-item">
            <label className="settings-label">{cat.name}</label>
            {prompts.map((p) => (
              <div key={p.id} className="settings-item">
                {editingId === p.id ? (
                  <>
                    <div className="settings-row">
                      <input
                        value={draft.name ?? ""}
                        onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                      />
                      <select
                        value={draft.category_id ?? ""}
                        onChange={(e) => setDraft((d) => ({ ...d, category_id: e.target.value }))}
                      >
                        {data.categories.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>
                      <select
                        value={draft.context ?? "global"}
                        onChange={(e) => setDraft((d) => ({ ...d, context: e.target.value as Prompt["context"] }))}
                      >
                        {CONTEXTS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                      </select>
                    </div>
                    <input
                      placeholder="Description"
                      value={draft.description ?? ""}
                      onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                    />
                    <textarea
                      rows={5}
                      value={draft.text ?? ""}
                      onChange={(e) => setDraft((d) => ({ ...d, text: e.target.value }))}
                    />
                    <div className="settings-row">
                      <button onClick={() => saveEdit(p.id)}>Save</button>
                      <button onClick={() => setEditingId(null)}>Cancel</button>
                    </div>
                  </>
                ) : (
                  <div className="settings-row">
                    <strong>{p.name}</strong>
                    <span className="hint">({p.code}, {p.context})</span>
                    <span className="hint">{p.description}</span>
                    <button onClick={() => startEdit(p)}>Edit</button>
                    {p.builtin ? (
                      <button onClick={() => resetPrompt(p.id)}>Reset to default</button>
                    ) : (
                      <button className="btn-danger-sm" onClick={() => deletePrompt(p.id)}>Delete</button>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        );
      })}

      {!showAddPrompt ? (
        <button onClick={() => setShowAddPrompt(true)}>Add prompt</button>
      ) : (
        <form onSubmit={createPrompt} className="settings-item">
          <div className="settings-row">
            <input
              placeholder="code (unique, e.g. suggest-tags)"
              value={newPrompt.code}
              onChange={(e) => setNewPrompt((p) => ({ ...p, code: e.target.value }))}
            />
            <input
              placeholder="Display name"
              value={newPrompt.name}
              onChange={(e) => setNewPrompt((p) => ({ ...p, name: e.target.value }))}
            />
            <select
              value={newPrompt.category_id}
              onChange={(e) => setNewPrompt((p) => ({ ...p, category_id: e.target.value }))}
            >
              {data.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <select
              value={newPrompt.context}
              onChange={(e) => setNewPrompt((p) => ({ ...p, context: e.target.value }))}
            >
              {CONTEXTS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <input
            placeholder="Description"
            value={newPrompt.description}
            onChange={(e) => setNewPrompt((p) => ({ ...p, description: e.target.value }))}
          />
          <textarea
            rows={5}
            placeholder="Prompt text — use {photo_id} / {user_nsid} where needed"
            value={newPrompt.text}
            onChange={(e) => setNewPrompt((p) => ({ ...p, text: e.target.value }))}
          />
          <div className="settings-row">
            <button type="submit" disabled={!newPrompt.code.trim() || !newPrompt.name.trim() || !newPrompt.text.trim()}>
              Save prompt
            </button>
            <button type="button" onClick={() => setShowAddPrompt(false)}>Cancel</button>
          </div>
        </form>
      )}

      <h3>Substitution variables</h3>
      <p className="hint">Click a variable to copy it, then paste it into a prompt's text.</p>
      <div className="settings-item">
        {data.variables.map((v: PromptVariable) => (
          <div key={v.code} className="settings-row">
            <button type="button" onClick={() => copyVar(v.code)}><code>{"{" + v.code + "}"}</code></button>
            <strong>{v.label}</strong>
            <span className="hint">{v.description}</span>
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
        <p className="hint">
          Only variables with matching backend support (currently <code>{"{photo_id}"}</code> and{" "}
          <code>{"{user_nsid}"}</code>) are actually substituted — others are documentation only.
        </p>
      </div>
    </div>
  );
}

import { FormEvent, useEffect, useState } from "react";
import { getJSON, postJSON } from "../api";

interface Setting {
  key: string;
  label: string;
  description: string;
  default: string;
  value: string;
}

export function SettingsPage() {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getJSON<{ settings: Setting[] }>("/api/settings")
      .then((r) => {
        setSettings(r.settings);
        setValues(Object.fromEntries(r.settings.map((s) => [s.key, s.value])));
      })
      .catch((e) => setError(e.message));
  }, []);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setMsg("");
    setError("");
    try {
      await postJSON("/api/settings", values);
      setMsg("Settings saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (error && settings.length === 0) return <div className="panel"><p className="error">{error}</p></div>;

  return (
    <div className="panel">
      <h2>Settings</h2>
      {error && <p className="error">{error}</p>}
      {msg && <p className="hint">{msg}</p>}
      <form onSubmit={save}>
        {settings.map((s) => (
          <div key={s.key} className="settings-item">
            <label htmlFor={`s-${s.key}`} className="settings-label">{s.label}</label>
            <p className="hint">{s.description}</p>
            <div className="settings-row">
              <input
                id={`s-${s.key}`}
                value={values[s.key] ?? ""}
                placeholder={s.default}
                onChange={(e) => setValues((v) => ({ ...v, [s.key]: e.target.value }))}
              />
              <span className="hint">default: {s.default}</span>
            </div>
          </div>
        ))}
        <div style={{ marginTop: 12 }}>
          <button type="submit">Save settings</button>
        </div>
      </form>
    </div>
  );
}

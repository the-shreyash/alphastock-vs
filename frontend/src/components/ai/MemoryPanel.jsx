import { useState, useEffect } from "react";
import { Brain, Check, Pencil } from "lucide-react";
import { toast } from "sonner";
import api from "../../services/api";

/**
 * MemoryPanel — surfaces and edits AI User Memory (risk, goals, sectors,
 * favourites, experience, notes) from GET/PUT /api/ai/memory. This is the
 * transparency contract of AI_AGENT_SYSTEM.md → AI Memory: the user can always
 * see and correct what the assistant remembers about them.
 */
const LEVELS = ["beginner", "intermediate", "advanced"];
const RISKS = ["conservative", "moderate", "aggressive"];

export default function MemoryPanel() {
  const [memory, setMemory] = useState(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({});

  useEffect(() => {
    api.get("/ai/memory")
      .then((r) => { setMemory(r.data); setForm(toForm(r.data)); })
      .catch(() => setMemory({}));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        risk_preference: form.risk_preference || null,
        experience_level: form.experience_level || null,
        goals: form.goals || null,
        notes: form.notes || null,
        preferred_sectors: splitList(form.preferred_sectors),
        favorite_companies: splitList(form.favorite_companies),
      };
      const { data } = await api.put("/ai/memory", payload);
      setMemory(data);
      setForm(toForm(data));
      setEditing(false);
      toast.success("AI memory updated");
    } catch {
      toast.error("Could not save memory");
    } finally {
      setSaving(false);
    }
  };

  if (!memory) return <div className="glass-card p-5"><div className="h-24 rounded-xl skeleton" /></div>;

  return (
    <div className="glass-card p-5" data-testid="ai-memory-panel">
      <div className="flex items-center justify-between mb-3">
        <h3 className="eyebrow flex items-center gap-2">
          <Brain size={13} style={{ color: "var(--ai-accent)" }} /> AI Memory
        </h3>
        {!editing && (
          <button onClick={() => setEditing(true)} className="text-[11px] flex items-center gap-1" style={{ color: "var(--ai-accent)" }}>
            <Pencil size={11} /> Edit
          </button>
        )}
      </div>

      {editing ? (
        <div className="space-y-3">
          <Select label="Risk preference" value={form.risk_preference} options={RISKS} onChange={(v) => setForm({ ...form, risk_preference: v })} />
          <Select label="Experience" value={form.experience_level} options={LEVELS} onChange={(v) => setForm({ ...form, experience_level: v })} />
          <Field label="Goals" value={form.goals} onChange={(v) => setForm({ ...form, goals: v })} placeholder="e.g. long-term wealth" />
          <Field label="Preferred sectors" value={form.preferred_sectors} onChange={(v) => setForm({ ...form, preferred_sectors: v })} placeholder="Banking, IT" />
          <Field label="Favourite companies" value={form.favorite_companies} onChange={(v) => setForm({ ...form, favorite_companies: v })} placeholder="RELIANCE, TCS" />
          <Field label="Notes" value={form.notes} onChange={(v) => setForm({ ...form, notes: v })} placeholder="Anything the AI should remember" />
          <div className="flex gap-2 pt-1">
            <button onClick={save} disabled={saving} className="btn-primary flex-1 justify-center py-2 text-[12px]">
              <Check size={13} /> {saving ? "Saving…" : "Save"}
            </button>
            <button onClick={() => { setEditing(false); setForm(toForm(memory)); }} className="btn-secondary py-2 text-[12px] px-3">Cancel</button>
          </div>
        </div>
      ) : (
        <dl className="space-y-2 text-[12px]">
          <Row label="Risk" value={memory.risk_preference} />
          <Row label="Experience" value={memory.experience_level} />
          <Row label="Goals" value={memory.goals} />
          <Row label="Sectors" value={(memory.preferred_sectors || []).join(", ")} />
          <Row label="Favourites" value={(memory.favorite_companies || []).join(", ")} />
          <Row label="Notes" value={memory.notes} />
          {(memory.lessons || []).length > 0 && (
            <div className="pt-2 mt-1" style={{ borderTop: "1px solid var(--border)" }}>
              <p className="text-[10px] uppercase tracking-wide mb-1.5" style={{ color: "var(--text-muted)" }}>Lessons learned</p>
              {memory.lessons.slice(-3).map((l, i) => (
                <p key={i} className="text-[11px] mb-1" style={{ color: "var(--text-secondary)" }}>• {l.lesson}</p>
              ))}
            </div>
          )}
        </dl>
      )}
    </div>
  );
}

function toForm(m) {
  return {
    risk_preference: m.risk_preference || "",
    experience_level: m.experience_level || "beginner",
    goals: m.goals || "",
    notes: m.notes || "",
    preferred_sectors: (m.preferred_sectors || []).join(", "),
    favorite_companies: (m.favorite_companies || []).join(", "),
  };
}

const splitList = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);

function Row({ label, value }) {
  return (
    <div className="flex justify-between gap-3">
      <dt style={{ color: "var(--text-muted)" }}>{label}</dt>
      <dd className="text-right truncate" style={{ color: value ? "var(--text-secondary)" : "var(--text-muted)" }}>
        {value || "—"}
      </dd>
    </div>
  );
}

function Field({ label, value, onChange, placeholder }) {
  return (
    <label className="block">
      <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{label}</span>
      <input className="search-input w-full mt-1 text-[12px]" value={value} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)} style={{ paddingLeft: 12 }} />
    </label>
  );
}

function Select({ label, value, options, onChange }) {
  return (
    <label className="block">
      <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{label}</span>
      <select className="search-input w-full mt-1 text-[12px] capitalize" value={value || ""} onChange={(e) => onChange(e.target.value)} style={{ paddingLeft: 12 }}>
        <option value="">—</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

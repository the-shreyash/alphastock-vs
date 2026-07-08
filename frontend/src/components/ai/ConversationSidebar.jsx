import { Plus, MessageSquare, Trash2 } from "lucide-react";

/**
 * ConversationSidebar — Conversation History. Lists the user's chat sessions
 * (GET /api/ai/conversations), lets them switch, start a new one, or delete.
 * Purely presentational; state + API calls live in the useAIWorkspace hook.
 */
export default function ConversationSidebar({ conversations, activeId, onSelect, onNew, onDelete, loading }) {
  return (
    <div className="glass-card flex flex-col" data-testid="conversation-sidebar" style={{ maxHeight: "calc(100vh - 280px)" }}>
      <div className="p-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <button onClick={onNew} className="btn-primary w-full justify-center py-2 text-[12px]" data-testid="new-conversation-btn">
          <Plus size={14} /> New chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {loading ? (
          [1, 2, 3].map((i) => <div key={i} className="h-12 rounded-xl skeleton" />)
        ) : conversations.length === 0 ? (
          <p className="text-[11px] text-center py-6" style={{ color: "var(--text-muted)" }}>
            No conversations yet. Start chatting to build your history.
          </p>
        ) : (
          conversations.map((c) => {
            const active = c.session_id === activeId;
            return (
              <div
                key={c.session_id}
                onClick={() => onSelect(c.session_id)}
                className="group flex items-start gap-2 p-2.5 rounded-xl cursor-pointer transition-all"
                style={{
                  background: active ? "var(--ai-accent-soft)" : "transparent",
                  border: active ? "1px solid var(--ai-accent-glow)" : "1px solid transparent",
                }}
                onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "var(--hover)"; }}
                onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
              >
                <MessageSquare size={14} className="shrink-0 mt-0.5" style={{ color: active ? "var(--ai-accent)" : "var(--text-muted)" }} />
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{c.title}</p>
                  <p className="text-[10px] truncate" style={{ color: "var(--text-muted)" }}>{c.message_count} messages</p>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); onDelete(c.session_id); }}
                  className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded-md shrink-0"
                  style={{ color: "var(--text-muted)" }}
                  title="Delete conversation"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

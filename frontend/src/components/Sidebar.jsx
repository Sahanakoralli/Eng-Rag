import { useMemo, useState } from "react";
import { useAuth } from "../AuthGate.jsx";
import { LogoMark } from "./Art.jsx";

function groupLabel(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return "Previous 7 days";
  return "Older";
}

export default function Sidebar({ history, activeId, onNew, onOpen, onDelete, open, onClose }) {
  const [q, setQ] = useState("");
  const { user, logout } = useAuth();
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = needle ? history.filter((c) => c.title.toLowerCase().includes(needle) || c.preview.toLowerCase().includes(needle)) : history;
    const out = new Map();
    for (const c of list) {
      const g = groupLabel(c.updated_at);
      if (!out.has(g)) out.set(g, []);
      out.get(g).push(c);
    }
    return [...out.entries()];
  }, [history, q]);

  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={"sidebar" + (open ? " open" : "")}>
        <div className="brand"><LogoMark size={30} />RAG QA</div>
        <button className="primary new" onClick={onNew}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg> New chat
        </button>
        <input className="search" type="search" placeholder="Search chats…" value={q} onChange={(e) => setQ(e.target.value)} />
        <nav className="history">
          {groups.length === 0 && <div className="hint pad">{q ? "No matching chats." : "Your previous chats will appear here."}</div>}
          {groups.map(([label, items]) => (
            <div key={label}>
              <div className="group">{label}</div>
              {items.map((c) => (
                <div key={c.session_id} className={"chat-item" + (c.session_id === activeId ? " active" : "")}
                     onClick={() => onOpen(c.session_id)} title={c.title}>
                  <span className="chat-title">{c.title}</span>
                  <button className="x" aria-label="Delete chat"
                          onClick={(e) => { e.stopPropagation(); if (confirm("Delete this chat?")) onDelete(c.session_id); }}>
                    <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>
                  </button>
                </div>
              ))}
            </div>
          ))}
        </nav>
        {user && (
          <div className="account">
            <span className="avatar-u" aria-hidden="true">{(user.username || "?").slice(0, 1).toUpperCase()}</span>
            <span className="who" title={user.email}>{user.username}</span>
            <a className="link" href="#about">About</a>
            <button className="link" onClick={logout}>Log out</button>
          </div>
        )}
      </aside>
    </>
  );
}

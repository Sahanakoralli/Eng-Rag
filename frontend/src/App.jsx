import { useCallback, useEffect, useRef, useState } from "react";
import { api, newSessionId, getKey, setKey } from "./api.js";
import Sidebar from "./components/Sidebar.jsx";
import ChatView from "./components/ChatView.jsx";
import Composer from "./components/Composer.jsx";
import DocsPanel from "./components/DocsPanel.jsx";

const SEL_KEY = "engrag_selected_files";
const CORPUS_KEY = "engrag_corpus";
const loadSel = () => { try { return new Set(JSON.parse(localStorage.getItem(SEL_KEY) || "[]")); } catch { return new Set(); } };

// Backend turns -> UI messages (used when an old chat is re-opened)
function turnsToMessages(turns) {
  return turns.flatMap((t, i) => [
    { id: `u${i}`, role: "user", text: t.question },
    { id: `a${i}`, role: "bot", text: t.answer, refused: t.refused, status: t.status, sources: (t.sources || []).map((s) => ({ ...s, linked: s.linked ?? s.linked_from })) },
  ]);
}

export default function App() {
  const [history, setHistory] = useState([]);
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [files, setFiles] = useState([]);
  const [selected, setSelected] = useState(loadSel);
  const [corpus, setCorpus] = useState(() => localStorage.getItem(CORPUS_KEY) || "");  // "uploads" | "library" ("" = not chosen yet)
  const [libraryAvailable, setLibraryAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [needsKey, setNeedsKey] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [docsOpen, setDocsOpen] = useState(true);
  const [notice, setNotice] = useState("");
  const pollRef = useRef(null);

  const refreshHistory = useCallback(() => api.history().then(setHistory).catch(() => {}), []);

  const refreshFiles = useCallback(async () => {
    try {
      const list = await api.files();
      setFiles(list);
      return list;
    } catch (e) {
      if (e.status === 401) setNeedsKey(true);
      return [];
    }
  }, []);

  // initial load
  useEffect(() => {
    api.health().then((h) => {
      setNeedsKey(h.auth_required && !getKey());
      setLibraryAvailable(!!h.library_available);
      // first visit: use the pre-indexed library when there is one, otherwise uploaded files
      setCorpus((c) => (c === "library" && !h.library_available ? "uploads" : c || (h.library_available ? "library" : "uploads")));
    }).catch(() => setCorpus((c) => c || "uploads"));
    refreshHistory();
    refreshFiles();
  }, [refreshHistory, refreshFiles]);

  // poll while any file is still being indexed; auto-select files the moment they become ready
  useEffect(() => {
    clearTimeout(pollRef.current);
    if (files.some((f) => f.status === "queued" || f.status === "processing")) {
      pollRef.current = setTimeout(async () => {
        const before = new Set(files.filter((f) => f.status === "ready").map((f) => f.file_id));
        const list = await refreshFiles();
        const fresh = list.filter((f) => f.status === "ready" && !before.has(f.file_id)).map((f) => f.file_id);
        if (fresh.length) setSelected((s) => new Set([...s, ...fresh]));
      }, 2000);
    }
    return () => clearTimeout(pollRef.current);
  }, [files, refreshFiles]);

  useEffect(() => { localStorage.setItem(SEL_KEY, JSON.stringify([...selected])); }, [selected]);
  useEffect(() => { if (corpus) localStorage.setItem(CORPUS_KEY, corpus); }, [corpus]);

  const readyIds = files.filter((f) => f.status === "ready").map((f) => f.file_id);
  const activeIds = [...selected].filter((id) => readyIds.includes(id));

  const newChat = () => { setSessionId(newSessionId()); setMessages([]); setSidebarOpen(false); setNotice(""); };

  const openChat = async (id) => {
    setSidebarOpen(false);
    setNotice("");
    try {
      const s = await api.session(id);
      setSessionId(s.session_id);
      setMessages(turnsToMessages(s.turns));
    } catch (e) { setNotice(e.message); }
  };

  const removeChat = async (id) => {
    try { await api.deleteSession(id); } catch { /* already gone */ }
    if (id === sessionId) newChat();
    refreshHistory();
  };

  const ask = async (question) => {
    setBusy(true);
    setNotice("");
    setMessages((m) => [...m, { id: `u${Date.now()}`, role: "user", text: question }, { id: "pending", role: "bot", pending: true }]);
    try {
      const r = await api.ask(question, sessionId, activeIds, corpus || "uploads");
      setMessages((m) => [
        ...m.filter((x) => x.id !== "pending"),
        { id: `a${Date.now()}`, role: "bot", text: r.answer, refused: r.refused, status: r.status, seconds: r.seconds,
          sources: r.sources.map((s) => ({ n: s.n, citation: s.citation, section: s.section, cited: s.cited, linked: s.linked_from })) },
      ]);
      refreshHistory();
    } catch (e) {
      if (e.status === 401) setNeedsKey(true);
      setMessages((m) => [...m.filter((x) => x.id !== "pending"), { id: `e${Date.now()}`, role: "error", text: e.message }]);
    } finally { setBusy(false); }
  };

  const upload = async (fileList) => {
    for (const f of fileList) {
      try { await api.upload(f); setNotice(""); setCorpus("uploads"); }
      catch (e) { setNotice(`Upload failed for ${f.name}: ${e.message}`); }
    }
    refreshFiles();
  };

  const removeFile = async (id) => {
    try { await api.deleteFile(id); setSelected((s) => { const n = new Set(s); n.delete(id); return n; }); refreshFiles(); }
    catch (e) { setNotice(e.message); }
  };

  const toggle = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const [dragging, setDragging] = useState(false);
  const onDrop = (e) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) upload([...e.dataTransfer.files]); };

  return (
    <div className="app" onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
         onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }} onDrop={onDrop}>
      <Sidebar history={history} activeId={sessionId} onNew={newChat} onOpen={openChat} onDelete={removeChat}
               open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <main className="main">
        <header className="topbar">
          <button className="icon-btn menu" onClick={() => setSidebarOpen(true)} aria-label="Open history">☰</button>
          <div className="title">Chat with your documents</div>
          <button className="ghost" onClick={() => setDocsOpen((o) => !o)}>
            📄 Documents ({files.length}) {docsOpen ? "▾" : "▸"}
          </button>
        </header>
        {docsOpen && <DocsPanel files={files} selected={selected} onToggle={toggle} onDelete={removeFile} onUpload={upload}
                                corpus={corpus} onCorpus={setCorpus} libraryAvailable={libraryAvailable} />}
        {needsKey && (
          <div className="banner">
            This server requires an API key.{" "}
            <input type="password" placeholder="X-API-Key" defaultValue={getKey()}
                   onKeyDown={(e) => { if (e.key === "Enter") { setKey(e.target.value); setNeedsKey(false); refreshFiles(); refreshHistory(); } }} />
            <span className="hint"> press Enter</span>
          </div>
        )}
        {notice && <div className="banner error" onClick={() => setNotice("")}>{notice}</div>}
        <ChatView messages={messages} corpus={corpus} hasFiles={corpus === "library" || readyIds.length > 0} onPick={(q) => ask(q)} busy={busy} />
        <Composer busy={busy} onSend={ask} onUpload={upload} corpus={corpus} scopeCount={activeIds.length} totalReady={readyIds.length} />
        {dragging && <div className="drop">Drop files to upload</div>}
      </main>
    </div>
  );
}

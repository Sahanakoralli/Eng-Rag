import { useEffect, useRef, useState } from "react";

export default function Composer({ busy, onSend, onUpload, corpus, scopeCount, totalReady }) {
  const [text, setText] = useState("");
  const ta = useRef(null);
  const pick = useRef(null);

  useEffect(() => {  // auto-grow textarea
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, [text]);

  const send = () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    onSend(q);
  };

  const scope = corpus === "library" ? "Searching the indexed library" : totalReady === 0 ? "No documents ready" : scopeCount === 0 ? `Searching all ${totalReady} file${totalReady > 1 ? "s" : ""}` : `Searching ${scopeCount} selected file${scopeCount > 1 ? "s" : ""}`;

  return (
    <footer className="composer">
      <div className="box">
        <button className="icon-btn" title="Upload a document" onClick={() => pick.current.click()}>📎</button>
        <input ref={pick} type="file" multiple hidden accept=".pdf,.docx,.pptx,.html,.htm,.md,.txt"
               onChange={(e) => { if (e.target.files.length) onUpload([...e.target.files]); e.target.value = ""; }} />
        <textarea ref={ta} rows={1} value={text} placeholder="Ask a question about your documents…" disabled={busy}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="primary send" onClick={send} disabled={busy || !text.trim()}>{busy ? "…" : "Send"}</button>
      </div>
      <div className="hint scope">{scope} · Enter to send, Shift+Enter for a new line</div>
    </footer>
  );
}

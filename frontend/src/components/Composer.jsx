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
        <button className="icon-btn" title="Upload a document" aria-label="Upload a document" onClick={() => pick.current.click()}>
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="m20 11-8.2 8.2a5 5 0 0 1-7-7L13.5 3.5a3.3 3.3 0 0 1 4.7 4.7l-8.7 8.7a1.7 1.7 0 0 1-2.4-2.4L15 6.5" /></svg>
        </button>
        <input ref={pick} type="file" multiple hidden accept=".pdf,.docx,.pptx,.html,.htm,.md,.txt"
               onChange={(e) => { if (e.target.files.length) onUpload([...e.target.files]); e.target.value = ""; }} />
        <textarea ref={ta} rows={1} value={text} placeholder="Ask a question about your documents…" disabled={busy}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="primary send" onClick={send} disabled={busy || !text.trim()}>{busy ? "…" : <>Send<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 3 10 14M21 3l-7 18-4-7-7-4z" /></svg></>}</button>
      </div>
      <div className="hint scope">{scope} · Enter to send, Shift+Enter for a new line</div>
    </footer>
  );
}

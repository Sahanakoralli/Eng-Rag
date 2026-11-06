import { useRef } from "react";

const fmt = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");

export default function DocsPanel({ files, selected, onToggle, onDelete, onUpload, corpus, onCorpus, libraryAvailable }) {
  const pick = useRef(null);
  return (
    <section className="docs">
      {libraryAvailable && (
        <div className="scope-switch" role="tablist" aria-label="What to search">
          <button role="tab" aria-selected={corpus === "library"} className={corpus === "library" ? "on" : ""} onClick={() => onCorpus("library")}>
            <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM4 21V5M8 7h6" /></svg> Indexed library
          </button>
          <button role="tab" aria-selected={corpus === "uploads"} className={corpus === "uploads" ? "on" : ""} onClick={() => onCorpus("uploads")}>
            <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M7 3h7l5 5v13H7zM14 3v5h5" /></svg> My uploads
          </button>
        </div>
      )}
      <div className="docs-head">
        <span className="hint">
          {corpus === "library"
            ? "Questions search the documents you indexed with the CLI. Uploading a file switches to “My uploads”."
            : "Tick the documents to search. Nothing ticked = search all ready files."}
        </span>
        <button className="primary small" onClick={() => pick.current.click()}><svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 16V4M7 9l5-5 5 5M5 20h14" /></svg> Upload document</button>
        <input ref={pick} type="file" multiple hidden accept=".pdf,.docx,.pptx,.html,.htm,.md,.txt"
               onChange={(e) => { if (e.target.files.length) onUpload([...e.target.files]); e.target.value = ""; }} />
      </div>
      {files.length === 0 ? (
        <div className="hint">No documents yet. Upload a PDF, DOCX, PPTX, HTML, MD or TXT file, or drag it onto this window.</div>
      ) : (
        <div className={"chips" + (corpus === "library" ? " dim" : "")}>
          {files.map((f) => {
            const ready = f.status === "ready";
            return (
              <label key={f.file_id} className={"chip " + f.status} title={f.error || f.filename}>
                <input type="checkbox" disabled={!ready} checked={ready && selected.has(f.file_id)} onChange={() => onToggle(f.file_id)} />
                <span className="chip-name">{f.filename}</span>
                <span className={"badge " + f.status}>
                  {ready ? `${f.chunks} chunks` : f.status === "failed" ? "failed" : <><i className="spin" /> {f.status}</>}
                </span>
                <span className="hint">{fmt(f.size_bytes)}</span>
                <button className="x" aria-label="Remove file" onClick={(e) => { e.preventDefault(); if (confirm(`Remove ${f.filename}?`)) onDelete(f.file_id); }}>×</button>
              </label>
            );
          })}
        </div>
      )}
    </section>
  );
}

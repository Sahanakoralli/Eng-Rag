import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const STATUS = { partial: "Partial answer", not_found: "Not found in documents" };

export default function Message({ m }) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(null);

  if (m.role === "user") return <div className="row user"><div className="bubble user">{m.text}</div></div>;
  if (m.role === "error") return <div className="row"><div className="bubble error">⚠ {m.text}</div></div>;
  if (m.pending) return <div className="row"><div className="bubble bot"><span className="dots"><i /><i /><i /></span></div></div>;

  const sources = m.sources || [];
  const cited = sources.filter((s) => s.cited);
  // make [1] / [2, 3] in the answer clickable: they open the source list and highlight the entry
  const text = (m.text || "").replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_, nums) =>
    nums.split(",").map((n) => `[${n.trim()}](#src-${n.trim()})`).join(" "));

  return (
    <div className="row">
      <div className={"bubble bot" + (m.refused ? " refused" : "")}>
        {STATUS[m.status] && <div className={"tag " + m.status}>{STATUS[m.status]}</div>}
        <div className="md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => href?.startsWith("#src-")
                ? <button className="cite" onClick={() => { setOpen(true); setHi(Number(href.slice(5))); }}>{children}</button>
                : <a href={href} target="_blank" rel="noreferrer">{children}</a>,
            }}>{text}</ReactMarkdown>
        </div>
        {sources.length > 0 && (
          <div className="sources">
            <button className="link" onClick={() => setOpen((o) => !o)}>
              {open ? "▾" : "▸"} Sources ({cited.length ? `${cited.length} cited of ${sources.length}` : sources.length})
            </button>
            {open && sources.map((s) => (
              <div key={s.n} className={"src" + (s.cited ? " cited" : "") + (hi === s.n ? " hi" : "")}>
                <b>[{s.n}]</b> {s.cited && "★ "}{s.citation}
                {s.section && <span className="hint"> · {s.section}</span>}
                {s.linked && <span className="hint"> · linked from {s.linked}</span>}
              </div>
            ))}
          </div>
        )}
        {m.seconds && <div className="hint time">{m.seconds}s</div>}
      </div>
    </div>
  );
}

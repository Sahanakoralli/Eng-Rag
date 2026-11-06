import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { RobotAvatar } from "./Art.jsx";

const Avatar = () => <span className="avatar"><RobotAvatar /></span>;

const STATUS = { partial: "Partial answer", not_found: "Not found in documents" };

export default function Message({ m }) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(null);

  if (m.role === "user") return <div className="row user"><div className="bubble user">{m.text}</div></div>;
  if (m.role === "error") return <div className="row bot"><Avatar /><div className="bubble error">⚠ {m.text}</div></div>;
  if (m.pending) return <div className="row bot"><Avatar /><div className="bubble bot"><span className="dots"><i /><i /><i /></span></div></div>;

  const sources = m.sources || [];
  const cited = sources.filter((s) => s.cited);
  // make [1] / [2, 3] in the answer clickable: they open the source list and highlight the entry
  const text = (m.text || "").replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_, nums) =>
    nums.split(",").map((n) => `[${n.trim()}](#src-${n.trim()})`).join(" "));

  return (
    <div className="row bot">
      <Avatar />
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
              <svg className={"chev" + (open ? " open" : "")} viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6" /></svg> Sources ({cited.length ? `${cited.length} cited of ${sources.length}` : sources.length})
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

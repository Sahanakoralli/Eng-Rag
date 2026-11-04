import { useEffect, useRef } from "react";
import Message from "./Message.jsx";

const SUGGESTIONS = ["Summarize this document", "What are the key requirements or specifications?", "List any safety warnings"];

export default function ChatView({ messages, corpus, hasFiles, onPick, busy }) {
  const end = useRef(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="chat empty">
        <div className="hero">
          <h1>What would you like to know?</h1>
          <p className="hint">{corpus === "library" ? "Ask a question about your indexed library." : hasFiles ? "Ask a question about your uploaded documents." : "Upload a document first (📎 or drag a file in), then ask questions about it."}</p>
          {hasFiles && corpus !== "library" && (
            <div className="suggest">
              {SUGGESTIONS.map((s) => <button key={s} disabled={busy} onClick={() => onPick(s)}>{s}</button>)}
            </div>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="chat">
      <div className="thread">
        {messages.map((m) => <Message key={m.id} m={m} />)}
        <div ref={end} />
      </div>
    </div>
  );
}

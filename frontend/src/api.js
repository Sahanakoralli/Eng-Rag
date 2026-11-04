// Thin wrapper around the FastAPI backend. Paths are relative: Vite proxies them in dev,
// and in production FastAPI serves this app, so they hit the same server.
const KEY = "engrag_api_key";
export const getKey = () => localStorage.getItem(KEY) || "";
export const setKey = (k) => (k ? localStorage.setItem(KEY, k) : localStorage.removeItem(KEY));

async function call(path, opt = {}) {
  const headers = { ...(opt.headers || {}) };
  if (getKey()) headers["X-API-Key"] = getKey();
  const r = await fetch(path, { ...opt, headers });
  const text = await r.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  if (!r.ok) {
    const detail = data && data.detail;
    const err = new Error(typeof detail === "string" ? detail : r.statusText || "Request failed");
    err.status = r.status;
    throw err;
  }
  return data;
}

const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => fetch("/health").then((r) => r.json()),
  history: () => call("/history"),
  session: (id) => call(`/sessions/${encodeURIComponent(id)}`),
  deleteSession: (id) => call(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  files: () => call("/files"),
  upload: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return call("/files", { method: "POST", body: fd });
  },
  deleteFile: (id) => call(`/files/${id}`, { method: "DELETE" }),
  ask: (question, sessionId, fileIds, corpus = "uploads") =>
    call("/ask", json({
      question, session_id: sessionId, corpus,
      file_ids: corpus === "uploads" && fileIds?.length ? fileIds : null,
    })),
};

export const newSessionId = () =>
  (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "").slice(0, 12) : Math.random().toString(36).slice(2, 14));

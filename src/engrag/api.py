"""Chat with your documents: a REST API plus a small web page.

    python -m engrag.cli serve            # then open http://127.0.0.1:8000

Flow: create an account (POST /auth/signup) or log in (POST /auth/login) -> upload a file (POST /files) -> it is
parsed, chunked and indexed in the background -> ask questions about it (POST /ask). Every route except /health and
/auth/* needs the token from signup/login in an "Authorization: Bearer <token>" header.
Every user has their own files and chat history. Accounts and history live in data/users.db (SQLite); the model's
follow-up context is kept per user in data/chats/, and every answer is also appended to one log file.
Interactive docs: http://127.0.0.1:8000/docs
"""
import json
import queue
import re
import secrets
import shutil
import threading
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from engrag import auth as accounts
from engrag import sms
from engrag.config import settings
from engrag.generation.chat import Session, chat_turn, safe_name
from engrag.ingestion import index as idx
from engrag.ingestion.parse import SUPPORTED_EXTS
from engrag.retrieval.search import Retriever


# ------------------------------------------------------------------ helpers
class LockedClient:
    """Qdrant's local mode is not built for concurrent threads, so every (short) call is serialised.
    Slow work (parsing, embedding, LLM calls) happens outside the lock."""

    def __init__(self, client):
        self._c, self._lock = client, threading.RLock()

    def __getattr__(self, name):
        attr = getattr(self._c, name)
        if not callable(attr):
            return attr

        def locked(*a, **k):
            with self._lock:
                return attr(*a, **k)
        return locked


def _safe_filename(name: str) -> str:
    name = Path((name or "").replace("\\", "/")).name  # drop any folder part ("../../x.pdf" -> "x.pdf")
    name = re.sub(r"[^\w.\- ]", "_", name).strip(" .")
    return name or "upload"


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ request / response models
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(None, description="Reuse the id from an earlier answer to ask follow-ups")
    file_ids: list[str] | None = Field(None, description="Only search these uploaded files (default: all uploads)")
    corpus: Literal["uploads", "library"] = "uploads"
    k: int = Field(6, ge=1, le=20, description="Chunks given to the LLM")
    mode: Literal["hybrid", "dense", "sparse"] = "hybrid"
    expand: bool = True
    rerank: bool = False
    history_turns: int = Field(4, ge=0, le=10)
    filters: dict[str, str | list[str]] | None = None
    include_text: bool = False

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v.strip()


class SignupRequest(BaseModel):
    email: str
    username: str
    password: str
    phone: str

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return accounts.validate_email(v)

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        return accounts.validate_username(v)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return accounts.validate_password(v)

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str) -> str:
        return accounts.normalize_phone(v)


class SignupVerifyRequest(SignupRequest):
    code: str

    @field_validator("code")
    @classmethod
    def _check_code(cls, v: str) -> str:
        return accounts.validate_otp_code(v)


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254, description="Username or email")
    password: str = Field(min_length=1, max_length=128)


class OtpRequest(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str) -> str:
        return accounts.normalize_phone(v)


class OtpVerifyRequest(OtpRequest):
    code: str

    @field_validator("code")
    @classmethod
    def _check_code(cls, v: str) -> str:
        return accounts.validate_otp_code(v)


class SourceOut(BaseModel):
    n: int
    doc_id: str
    filename: str | None = None
    citation: str
    page_start: int | None = None
    page_end: int | None = None
    section: str = ""
    cited: bool
    linked_from: str = ""
    text: str | None = None


class AskResponse(BaseModel):
    session_id: str
    turn: int
    question: str
    standalone_question: str
    answer: str
    refused: bool
    status: str = Field("", description="answered | partial | not_found")
    cited_docs: list[str]
    sources: list[SourceOut]
    seconds: float


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    file_ids: list[str] | None = None
    corpus: Literal["uploads", "library"] = "uploads"
    k: int = Field(5, ge=1, le=50)
    mode: Literal["hybrid", "dense", "sparse"] = "hybrid"


# ------------------------------------------------------------------ app factory
def create_app(client=None, embedder=None, library_collection=None, uploads_dir=None, chats_dir=None,
               log_path=None, api_key=None, sync_ingest=False, max_upload_mb=None, ocr=None, db_path=None) -> FastAPI:
    """Pass `client`/`embedder` to inject your own (tests do); otherwise they are created on startup."""
    S = type("State", (), {})()
    S.uploads_dir = Path(uploads_dir or settings.uploads_dir)
    S.chats_dir = Path(chats_dir or settings.chats_dir)
    S.log_path = Path(log_path or settings.qa_log_path)
    S.api_key = settings.api_key if api_key is None else api_key
    S.store = accounts.UserStore(db_path or S.chats_dir.parent / "users.db")  # accounts + per-user chat history
    S.throttle = accounts.LoginThrottle()
    S.otp_throttle = accounts.LoginThrottle(max_failures=10, window=3600)  # OTP requests per client address per hour
    S.max_bytes = int((settings.max_upload_mb if max_upload_mb is None else max_upload_mb) * 1024 * 1024)
    S.ocr = settings.upload_ocr if ocr is None else ocr
    S.uploads_collection = settings.uploads_collection
    S.library_collection = library_collection or settings.collection
    S.client, S.embedder, S.uploads, S.library = client, embedder, None, None
    S.injected = client is not None
    S.queue = queue.Queue()
    S.log_lock = threading.Lock()
    S.session_locks, S.session_locks_guard = defaultdict(threading.Lock), threading.Lock()

    # ---------- storage of uploaded files
    def meta_path(fid: str) -> Path:
        return S.uploads_dir / fid / "meta.json"

    def read_meta(fid: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", fid or ""):
            return None
        p = meta_path(fid)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def update_meta(fid: str, **changes) -> dict:
        m = read_meta(fid)
        m.update(changes)
        _write_json(meta_path(fid), m)
        return m

    def all_meta() -> list[dict]:
        out = []
        for p in S.uploads_dir.glob("*/meta.json") if S.uploads_dir.exists() else []:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return sorted(out, key=lambda m: m.get("uploaded_at", 0), reverse=True)

    def visible(m: dict, user: dict) -> bool:
        """A file belongs to whoever uploaded it. Files from before accounts existed have no owner: everyone sees them."""
        return m.get("owner_id") in (None, user["id"])

    def process_file(fid: str) -> None:
        """Parse, chunk, embed and index one uploaded file (runs in the background worker)."""
        from engrag.ingestion.pipeline import ingest

        meta = read_meta(fid)
        if not meta:
            return
        update_meta(fid, status="processing", error="")
        manifest = S.uploads_dir.parent / "uploads_manifest.json"
        try:
            stats = ingest(S.uploads_dir, embedder=S.embedder, client=S.client, ocr=S.ocr, log=lambda *_: None,
                           collection=S.uploads_collection, manifest_path=manifest,
                           processed_dir=S.uploads_dir.parent / "uploads_processed", only={meta["doc_id"]})
            if stats["failed"]:
                raise RuntimeError(stats["failed"][0][1])
            entry = json.loads(manifest.read_text(encoding="utf-8")).get(meta["doc_id"], {})
            update_meta(fid, status="ready", chunks=entry.get("chunks", 0), error="")
        except Exception as e:  # noqa: BLE001 - report on the file, never crash the worker
            update_meta(fid, status="failed", error=f"{type(e).__name__}: {e}"[:500])

    def worker() -> None:
        while True:
            fid = S.queue.get()
            if fid is None:
                return
            process_file(fid)

    # ---------- lifecycle
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        S.uploads_dir.mkdir(parents=True, exist_ok=True)
        if not S.injected:
            from engrag.ingestion.embed import Embedder

            S.client = idx.get_client(settings.qdrant_path)
            S.embedder = Embedder()
            S.embedder.dense_query("warm up")  # load models now, not on the first user's request
        S.client = LockedClient(S.client)
        S.uploads = Retriever(S.client, S.embedder, collection=S.uploads_collection)
        has_library = S.client.collection_exists(S.library_collection)
        S.library = Retriever(S.client, S.embedder, collection=S.library_collection) if has_library else None
        thread = None
        if not sync_ingest:
            thread = threading.Thread(target=worker, daemon=True, name="ingest-worker")
            thread.start()
            for m in all_meta():  # files interrupted by a restart: process them again
                if m.get("status") in ("queued", "processing"):
                    update_meta(m["file_id"], status="queued")
                    S.queue.put(m["file_id"])
        yield
        if thread:
            S.queue.put(None)
            thread.join(timeout=5)
        if not S.injected:
            S.client._c.close()

    app = FastAPI(title="Chat with your documents", lifespan=lifespan)

    def require_key(x_api_key: str | None = Header(default=None)):
        if S.api_key and not (x_api_key and secrets.compare_digest(x_api_key, S.api_key)):
            raise HTTPException(401, "Missing or wrong X-API-Key header.")

    auth = [Depends(require_key)]  # optional server-wide access key (API_KEY); with it set, signup is invite-only
    bearer = HTTPBearer(auto_error=False)

    def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
        user = S.store.user_for_token(creds.credentials) if creds else None
        if user is None:
            raise HTTPException(401, "Please log in.", headers={"WWW-Authenticate": "Bearer"})
        return user

    def login_response(user: dict) -> dict:
        return {"access_token": S.store.issue_token(user["id"]), "token_type": "bearer", "user": user}

    # ---------- accounts
    def send_code(phone: str, request: Request) -> dict:
        """Text a one-time code to `phone`, with limits so nobody can make us pay for unlimited SMS."""
        ip = request.client.host if request.client else "?"
        wait = S.otp_throttle.blocked_for(ip)
        if wait:
            raise HTTPException(429, f"Too many OTP requests. Try again in {wait // 60 + 1} minute(s).",
                                headers={"Retry-After": str(wait)})
        S.otp_throttle.fail(ip)  # every request counts
        try:
            code = S.store.request_otp(phone)
        except accounts.OtpRateLimited as e:
            raise HTTPException(429, f"Please wait {e.wait} seconds before asking for another OTP.",
                                headers={"Retry-After": str(e.wait)}) from e
        try:
            sms.send_otp(phone, code)
        except sms.SmsError as e:
            S.store.cancel_otp(phone)
            raise HTTPException(502, "Could not send the SMS right now. Please try again.") from e
        return {"sent": True, "expires_in": accounts.OTP_TTL_SECONDS, "resend_in": accounts.OTP_RESEND_SECONDS}

    def check_free(req: SignupRequest) -> None:
        taken = S.store.taken_field(req.email, req.username, req.phone)
        if taken:
            raise HTTPException(409, f"That {'phone number' if taken == 'phone' else taken} is already registered.")

    @app.post("/auth/signup/otp", dependencies=auth)
    def signup_otp(req: SignupRequest, request: Request):
        """Signup step 1: check the details are valid and free, then text a code to the phone number."""
        check_free(req)
        return send_code(req.phone, request)

    @app.post("/auth/signup", status_code=201, dependencies=auth)
    def signup(req: SignupVerifyRequest):
        """Signup step 2: the code proves the phone number is yours, then the account is created."""
        check_free(req)
        if not S.store.verify_otp(req.phone, req.code):
            raise HTTPException(401, "Wrong or expired OTP.")
        try:
            user = S.store.create_user(req.email, req.username, req.password, req.phone)
        except accounts.UserExists as e:
            raise HTTPException(409, str(e)) from e
        return login_response(user)

    @app.post("/auth/login", dependencies=auth)
    def login(req: LoginRequest, request: Request):
        who = (request.client.host if request.client else "?", req.identifier.strip().lower())
        wait = S.throttle.blocked_for(who)
        if wait:
            raise HTTPException(429, f"Too many wrong passwords. Try again in {wait // 60 + 1} minute(s).",
                                headers={"Retry-After": str(wait)})
        user = S.store.authenticate(req.identifier, req.password)
        if user is None:
            S.throttle.fail(who)
            raise HTTPException(401, "Wrong username/email or password.")
        S.throttle.reset(who)
        return login_response(user)

    @app.post("/auth/otp/request", dependencies=auth)
    def otp_request(req: OtpRequest, request: Request):
        """Log in by OTP: text a code to a number that already has an account."""
        if S.store.user_for_phone(req.phone) is None:
            raise HTTPException(404, "No account uses this number. Sign up first.")
        return send_code(req.phone, request)

    @app.post("/auth/otp/verify", dependencies=auth)
    def otp_verify(req: OtpVerifyRequest):
        if not S.store.verify_otp(req.phone, req.code):
            raise HTTPException(401, "Wrong or expired OTP.")
        user = S.store.user_for_phone(req.phone)
        if user is None:
            raise HTTPException(401, "Wrong or expired OTP.")
        return login_response(user)

    @app.post("/auth/logout", dependencies=auth)
    def logout(creds: HTTPAuthorizationCredentials | None = Depends(bearer), user: dict = Depends(current_user)):
        S.store.revoke_token(creds.credentials)
        return {"logged_out": True}

    @app.get("/auth/me", dependencies=auth)
    def me(user: dict = Depends(current_user)):
        return user

    # ---------- pages
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home():
        index = dist / "index.html"
        return index.read_text(encoding="utf-8") if index.exists() else CHAT_PAGE

    @app.get("/health")
    def health():
        n_up = S.client.count(S.uploads_collection, exact=True).count if S.client.collection_exists(S.uploads_collection) else 0
        return {"status": "ok", "auth_required": bool(S.api_key), "login_required": True, "llm": f"{settings.llm_provider}/{settings.llm_model}",
                "uploaded_files": len(all_meta()), "uploaded_chunks": n_up, "library_available": S.library is not None}

    # ---------- files
    @app.post("/files", status_code=202, dependencies=auth)
    def upload_file(file: UploadFile = File(...), user: dict = Depends(current_user)):
        name = _safe_filename(file.filename)
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            raise HTTPException(415, f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(SUPPORTED_EXTS))}")
        fid = uuid.uuid4().hex[:12]
        folder = S.uploads_dir / fid
        folder.mkdir(parents=True, exist_ok=True)
        target, size = folder / name, 0
        try:
            with target.open("wb") as out:
                while chunk := file.file.read(1 << 20):
                    size += len(chunk)
                    if size > S.max_bytes:
                        raise HTTPException(413, f"File is larger than {S.max_bytes / 1024 / 1024:g} MB.")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(400, "The file is empty.")
            if ext == ".pdf" and not target.read_bytes()[:1024].lstrip().startswith(b"%PDF"):
                raise HTTPException(400, "This does not look like a real PDF file.")
        except HTTPException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        doc_id = f"{fid}/{name}"
        _write_json(folder / f"{name}.meta.json", {"file_id": fid, "filename": name})  # becomes chunk metadata
        meta = {"file_id": fid, "filename": name, "doc_id": doc_id, "size_bytes": size, "status": "queued",
                "chunks": 0, "error": "", "uploaded_at": time.time(), "owner_id": user["id"]}
        _write_json(meta_path(fid), meta)
        if sync_ingest:
            process_file(fid)
            return read_meta(fid)
        S.queue.put(fid)
        return meta

    @app.get("/files", dependencies=auth)
    def list_files(user: dict = Depends(current_user)):
        return [m for m in all_meta() if visible(m, user)]

    @app.get("/files/{file_id}", dependencies=auth)
    def get_file(file_id: str, user: dict = Depends(current_user)):
        m = read_meta(file_id)
        if not m or not visible(m, user):
            raise HTTPException(404, "Unknown file id.")
        return m

    @app.delete("/files/{file_id}", dependencies=auth)
    def delete_file(file_id: str, user: dict = Depends(current_user)):
        m = read_meta(file_id)
        if not m or not visible(m, user):
            raise HTTPException(404, "Unknown file id.")
        if m["status"] == "processing":
            raise HTTPException(409, "The file is being processed right now. Try again in a moment.")
        idx.delete_doc(S.client, S.uploads_collection, m["doc_id"])
        manifest = S.uploads_dir.parent / "uploads_manifest.json"
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.pop(m["doc_id"], None) is not None:
                _write_json(manifest, data)
        shutil.rmtree(S.uploads_dir / file_id, ignore_errors=True)
        return {"deleted": file_id}

    # ---------- asking
    def scope(corpus: str, file_ids: list[str] | None, user: dict):
        """Which retriever to use and which uploaded files to restrict it to (only the user's own files)."""
        if corpus == "library":
            if S.library is None:
                raise HTTPException(404, "No library index found. Ingest documents first (python -m engrag.cli ingest).")
            return S.library, {}
        if file_ids:
            for fid in file_ids:
                m = read_meta(fid)
                if not m or not visible(m, user):
                    raise HTTPException(404, f"Unknown file id '{fid}'.")
                if m["status"] != "ready":
                    raise HTTPException(409, f"'{m['filename']}' is not ready yet (status: {m['status']}"
                                             f"{': ' + m['error'] if m['error'] else ''}).")
            return S.uploads, {"file_id": list(file_ids)}
        mine = [m["file_id"] for m in all_meta() if visible(m, user) and m["status"] == "ready"]
        if not mine:
            raise HTTPException(409, "Upload a file first (POST /files), and wait until its status is 'ready'.")
        return S.uploads, {"file_id": mine}

    def sources_out(hits, cited, include_text: bool) -> list[SourceOut]:
        return [SourceOut(n=i, doc_id=h.doc_id, filename=h.payload.get("filename"), citation=h.citation,
                          page_start=h.page_start, page_end=h.page_end, section=h.section_path, cited=i in cited,
                          linked_from=h.payload.get("linked_from", ""), text=h.text if include_text else None)
                for i, h in enumerate(hits, 1)]

    @app.post("/ask", response_model=AskResponse, dependencies=auth)
    def ask(req: AskRequest, user: dict = Depends(current_user)):
        retriever, flt = scope(req.corpus, req.file_ids, user)
        filters = {**(req.filters or {}), **flt}  # flt wins: a user can never widen the search to other people's files
        sid = safe_name(req.session_id) if req.session_id else uuid.uuid4().hex[:12]
        key = f"u{user['id']}-{sid}"  # the model's follow-up context is stored per user, so ids can never collide or leak
        with S.session_locks_guard:
            lock = S.session_locks[key]
        t0 = time.time()
        with lock:  # one question at a time per conversation, so history stays in order
            sess = Session(key, S.chats_dir)
            try:
                a = chat_turn(req.question, retriever, sess, history_turns=req.history_turns, k=req.k, mode=req.mode,
                              expand=req.expand, rerank=req.rerank, filters=filters or None)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Could not produce an answer: {type(e).__name__}: {str(e)[:300]}") from e
            turn_no = len(sess.turns)
        seconds = round(time.time() - t0, 2)
        entry = {"ts": time.time(), "user_id": user["id"], "session_id": sid, "corpus": req.corpus, "file_ids": req.file_ids,
                 "question": a.question, "standalone_question": a.standalone, "answer": a.text, "refused": a.refused,
                 "cited_docs": a.cited_docs, "seconds": seconds}
        with S.log_lock:
            S.log_path.parent.mkdir(parents=True, exist_ok=True)
            with S.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        sources = sources_out(a.hits, set(a.cited), req.include_text)
        S.store.save_turn(user["id"], sid, question=a.question, answer=a.text, standalone=a.standalone or a.question,
                          refused=a.refused, status=a.status, cited_docs=a.cited_docs, seconds=seconds,
                          sources=[s.model_dump(exclude={"text"}) for s in sources])
        return AskResponse(session_id=sid, turn=turn_no, question=a.question, standalone_question=a.standalone or a.question,
                           answer=a.text, refused=a.refused, status=a.status, cited_docs=a.cited_docs,
                           sources=sources, seconds=seconds)

    @app.post("/search", dependencies=auth)
    def search(req: SearchRequest, user: dict = Depends(current_user)):
        """Retrieval only (no LLM): which chunks match this query?"""
        retriever, flt = scope(req.corpus, req.file_ids, user)
        hits = retriever.search(req.query, k=req.k, mode=req.mode, rerank=False, filters=flt or None)
        return [{**s.model_dump(), "score": round(h.score, 4)} for s, h in zip(sources_out(hits, set(), True), hits)]

    # ---------- stored conversations (each user sees only their own)
    @app.get("/sessions", dependencies=auth)
    def sessions(user: dict = Depends(current_user)):
        return [{"session_id": c["session_id"], "turns": c["turns"]} for c in S.store.list_chats(user["id"])]

    @app.get("/sessions/{session_id}", dependencies=auth)
    def get_session(session_id: str, user: dict = Depends(current_user)):
        chat = S.store.get_chat(user["id"], safe_name(session_id))
        if not chat:
            raise HTTPException(404, "Unknown session.")
        return chat

    @app.delete("/sessions/{session_id}", dependencies=auth)
    def delete_session(session_id: str, user: dict = Depends(current_user)):
        sid = safe_name(session_id)
        if not S.store.delete_chat(user["id"], sid):
            raise HTTPException(404, "Unknown session.")
        s = Session(f"u{user['id']}-{sid}", S.chats_dir)
        if s.path.exists():
            s.clear()
        return {"deleted": sid}

    @app.get("/history", dependencies=auth)
    def history(user: dict = Depends(current_user)):
        """Sidebar data: the user's chats with a title (first question), preview and last-updated time, newest first."""
        return S.store.list_chats(user["id"])

    @app.get("/log", dependencies=auth)
    def qa_log(limit: int = 50, user: dict = Depends(current_user)):
        """The user's most recent questions and answers across all their sessions."""
        if not S.log_path.exists():
            return []
        out, want = [], max(1, min(limit, 1000))
        for line in reversed(S.log_path.read_text(encoding="utf-8").splitlines()):
            try:
                e = json.loads(line) if line.strip() else None
            except json.JSONDecodeError:
                continue
            if e and e.get("user_id") == user["id"]:
                out.append(e)
                if len(out) >= want:
                    break
        return out[::-1]

    if (dist / "assets").exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    return app


app = create_app()


# ------------------------------------------------------------------ chat page (no build step, no external files)
CHAT_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chat with your documents</title>
<style>
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--text:#1d2330;--mut:#667085;--line:#e3e6ec;--acc:#2f6fed;--ok:#1a7f4b;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1c2028;--text:#e8eaf0;--mut:#98a2b3;--line:#2c313c;--acc:#6b9bff;--ok:#4cc38a;--bad:#ff7b72}}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text)}
header{padding:12px 20px;border-bottom:1px solid var(--line);background:var(--card);display:flex;align-items:center;gap:12px}
header b{flex:1;font-weight:600}
main{display:grid;grid-template-columns:300px 1fr;gap:16px;max-width:1100px;margin:16px auto;padding:0 16px;height:calc(100vh - 90px)}
@media (max-width:760px){main{grid-template-columns:1fr;height:auto}}
.side{display:flex;flex-direction:column;gap:16px;min-height:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;display:flex;flex-direction:column;min-height:0}
.side .card.grow{flex:1}
h2{font-size:13px;font-weight:600;color:var(--mut);margin:0 0 10px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:8px;padding:7px 12px;cursor:pointer}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}button:disabled{opacity:.5;cursor:default}
button:focus-visible,input:focus-visible{outline:2px solid var(--acc);outline-offset:1px}
input[type=text],input[type=password],input[type=email]{font:inherit;width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text)}
.authcard{max-width:380px;margin:48px auto;gap:12px}
.tabs{display:flex;gap:6px}.tabs button{flex:1}.tabs button.on{background:var(--acc);border-color:var(--acc);color:#fff}
label{display:block;font-size:13px;color:var(--mut)}label input{margin-top:4px}
#autherr,.formerr{color:var(--bad);font-size:13px;min-height:1.3em}
#chats{overflow:auto;display:flex;flex-direction:column;gap:4px;min-height:60px}
.item{display:flex;align-items:center;border:1px solid transparent;border-radius:8px}
.item:hover{background:var(--bg)}.item.on{border-color:var(--acc);background:var(--bg)}
.item .open{flex:1;min-width:0;text-align:left;border:0;background:none;padding:7px 8px}
.item .t{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13.5px}
.item .d{display:block;font-size:11.5px;color:var(--mut)}
.item .x,.file .x{border:0;background:none;padding:0 8px;color:var(--mut)}
#files{overflow:auto;margin-top:10px;display:flex;flex-direction:column;gap:6px;max-height:180px}
.file{display:flex;gap:8px;align-items:center;padding:7px 8px;border:1px solid var(--line);border-radius:8px;font-size:13px}
.file .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);color:var(--mut)}
.badge.ready{color:var(--ok)}.badge.failed{color:var(--bad)}
#chat{flex:1;overflow:auto;display:flex;flex-direction:column;gap:12px;padding-right:4px}
.msg{max-width:85%;padding:10px 13px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
.user{align-self:flex-end;background:var(--acc);color:#fff}.bot{align-self:flex-start;background:var(--bg);border:1px solid var(--line)}
.bot.refused{border-style:dashed}.err{align-self:flex-start;color:var(--bad)}
details{margin-top:8px;font-size:12.5px;color:var(--mut)}details div{padding:2px 0}.cited{color:var(--text);font-weight:600}
.row{display:flex;gap:8px;margin-top:12px}.hint{color:var(--mut);font-size:13px}
</style></head><body>
<header><b>Chat with your documents</b><span class="hint" id="who"></span><button id="logout" hidden>Log out</button></header>

<section id="auth" hidden>
 <form class="card authcard" id="authform">
  <div class="tabs"><button type="button" id="tab-login" class="on">Log in</button><button type="button" id="tab-signup">Sign up</button><button type="button" id="tab-otp">Phone OTP</button></div>
  <label id="row-email" hidden>Email<input type="email" id="f-email" autocomplete="email"></label>
  <label id="row-user"><span id="lbl-user">Username or email</span><input type="text" id="f-user" autocomplete="username" required></label>
  <label id="row-pass">Password<input type="password" id="f-pass" autocomplete="current-password" required></label>
  <div id="otpbox" hidden style="display:flex;flex-direction:column;gap:12px">
   <label>Mobile number (India)<input type="tel" id="f-phone" inputmode="numeric" autocomplete="tel-national" placeholder="10-digit number"></label>
   <label id="row-code" hidden>OTP<input type="text" id="f-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6"></label>
   <button type="button" id="resend" hidden>Resend OTP</button>
  </div>
  <label id="keybox" hidden>Server access key<input type="password" id="key" autocomplete="off"></label>
  <div id="autherr" role="alert"></div>
  <button class="primary" id="authbtn" type="submit">Log in</button>
 </form>
</section>

<main id="app" hidden>
 <div class="side">
  <section class="card grow">
   <h2>Your chats <button id="new" style="float:right;padding:2px 9px">New chat</button></h2>
   <div id="chats"></div>
  </section>
  <section class="card">
   <h2>Your files</h2>
   <input type="file" id="pick" accept=".pdf,.docx,.pptx,.html,.htm,.md,.txt">
   <div class="row"><button class="primary" id="up">Upload</button><span class="hint" id="upmsg"></span></div>
   <div id="files"></div>
  </section>
 </div>
 <section class="card">
  <h2>Conversation</h2>
  <div id="chat"></div>
  <div class="row"><input type="text" id="q" placeholder="Ask about your files..." autocomplete="off"><button class="primary" id="send">Send</button></div>
 </section>
</main>

<script>
const $=s=>document.querySelector(s);
let token=localStorage.getItem('engrag_token')||'',me=null,mode='login',sid=null,files=[],selected=new Set(),busy=false;
const newId=()=>Math.random().toString(36).slice(2,14);
const HINT='Upload a file, wait until it says ready, then ask a question. You can ask follow-ups.';
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
function errText(d,fallback){
  const x=d&&d.detail;
  if(Array.isArray(x))return x.map(i=>(i.loc&&i.loc.length?i.loc[i.loc.length-1]+': ':'')+String(i.msg).replace(/^Value error, /,'')).join('; ');
  return x||fallback}
async function api(path,opt={}){
  const h={...(opt.headers||{})};
  if(token)h.Authorization='Bearer '+token;
  const k=$('#key').value||sessionStorage.getItem('key')||'';if(k)h['X-API-Key']=k;
  const r=await fetch(path,{...opt,headers:h});
  const t=await r.text();let d;try{d=JSON.parse(t)}catch{d=t}
  if(r.status===401&&token&&!path.startsWith('/auth/')){leave('Your session expired. Log in again.');throw new Error('Not logged in')}
  if(!r.ok)throw new Error(errText(d,r.statusText));return d}

/* ---------- sign up / log in / log out ---------- */
let otpSent=false;const JSONH={'Content-Type':'application/json'};
function setMode(m){
  mode=m;const login=m==='login',otp=m==='otp',signup=m==='signup';
  for(const [id,name] of [['tab-login','login'],['tab-signup','signup'],['tab-otp','otp']])$('#'+id).classList.toggle('on',m===name);
  $('#row-email').hidden=!signup;$('#f-email').required=signup;
  $('#row-user').hidden=otp;$('#f-user').required=!otp;$('#row-pass').hidden=otp;$('#f-pass').required=!otp;
  $('#otpbox').hidden=login;$('#f-phone').required=!login;
  $('#lbl-user').textContent=login?'Username or email':'Username';
  $('#f-user').autocomplete='username';$('#f-pass').autocomplete=login?'current-password':'new-password';
  resetOtp();$('#autherr').textContent=''}
function resetOtp(){
  otpSent=false;$('#row-code').hidden=true;$('#f-code').required=false;$('#f-code').value='';$('#resend').hidden=true;
  $('#authbtn').textContent=mode==='login'?'Log in':mode==='signup'?'Send OTP to verify phone':'Send OTP'}
function signupBody(){
  return {email:$('#f-email').value.trim(),username:$('#f-user').value.trim(),password:$('#f-pass').value,phone:$('#f-phone').value.trim()}}
async function sendOtp(){
  const signup=mode==='signup';
  await api(signup?'/auth/signup/otp':'/auth/otp/request',{method:'POST',headers:JSONH,
    body:JSON.stringify(signup?signupBody():{phone:$('#f-phone').value.trim()})});
  otpSent=true;$('#row-code').hidden=false;$('#f-code').required=true;$('#f-code').focus();$('#resend').hidden=false;
  $('#authbtn').textContent=signup?'Verify and create account':'Verify and log in';
  $('#autherr').textContent='OTP sent by SMS. It is valid for 5 minutes.'}
async function otpStep(){
  $('#autherr').textContent='';$('#authbtn').disabled=true;
  try{
    if(!otpSent)await sendOtp();
    else{
      const signup=mode==='signup',code=$('#f-code').value.trim();
      const r=signup?await api('/auth/signup',{method:'POST',headers:JSONH,body:JSON.stringify({...signupBody(),code})})
        :await api('/auth/otp/verify',{method:'POST',headers:JSONH,body:JSON.stringify({phone:$('#f-phone').value.trim(),code})});
      token=r.access_token;localStorage.setItem('engrag_token',token);me=r.user;$('#f-pass').value='';$('#f-code').value='';enter()}
  }catch(err){$('#autherr').textContent=err.message}
  $('#authbtn').disabled=false}
$('#resend').onclick=async()=>{$('#autherr').textContent='';try{await sendOtp()}catch(err){$('#autherr').textContent=err.message}};
$('#f-phone').addEventListener('input',resetOtp);
$('#tab-login').onclick=()=>setMode('login');$('#tab-signup').onclick=()=>setMode('signup');$('#tab-otp').onclick=()=>setMode('otp');
$('#authform').addEventListener('submit',async e=>{
  e.preventDefault();
  if(mode!=='login')return otpStep();
  $('#autherr').textContent='';
  const login=mode==='login',user=$('#f-user').value.trim(),pass=$('#f-pass').value;
  const body=login?{identifier:user,password:pass}:{email:$('#f-email').value.trim(),username:user,password:pass};
  if($('#key').value)sessionStorage.setItem('key',$('#key').value);
  $('#authbtn').disabled=true;
  try{
    const r=await api(login?'/auth/login':'/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    token=r.access_token;localStorage.setItem('engrag_token',token);me=r.user;$('#f-pass').value='';enter()}
  catch(err){$('#autherr').textContent=err.message}
  $('#authbtn').disabled=false});
function leave(msg){
  token='';me=null;localStorage.removeItem('engrag_token');sid=null;files=[];selected.clear();
  $('#app').hidden=true;$('#logout').hidden=true;$('#who').textContent='';$('#auth').hidden=false;
  $('#autherr').textContent=msg||''}
$('#logout').onclick=async()=>{try{await api('/auth/logout',{method:'POST'})}catch(e){}leave('')};
function enter(){
  $('#auth').hidden=true;$('#app').hidden=false;$('#logout').hidden=false;$('#who').textContent=me.username;
  newChat();loadFiles()}

/* ---------- chat history ---------- */
function fmt(ts){const d=new Date(ts*1000),t=new Date();
  return d.toDateString()===t.toDateString()?d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):d.toLocaleDateString()}
async function loadChats(){
  let list=[];try{list=await api('/history')}catch(e){return}
  const box=$('#chats');box.replaceChildren();
  if(!list.length)box.append(el('div','hint','No chats yet. Your questions will show up here.'));
  for(const c of list){
    const row=el('div','item'+(c.session_id===sid?' on':''));
    const open=el('button','open');open.title=c.preview||c.title;
    open.append(el('span','t',c.title),el('span','d',fmt(c.updated_at)+'  \u00b7  '+c.turns+(c.turns===1?' question':' questions')));
    open.onclick=()=>openChat(c.session_id);
    const x=el('button','x','\u00d7');x.title='Delete this chat';x.setAttribute('aria-label','Delete chat');
    x.onclick=async()=>{if(!confirm('Delete this chat?'))return;
      try{await api('/sessions/'+encodeURIComponent(c.session_id),{method:'DELETE'});if(c.session_id===sid)newChat();loadChats()}catch(e){alert(e.message)}};
    row.append(open,x);box.append(row)}}
async function openChat(id){
  try{const d=await api('/sessions/'+encodeURIComponent(id));
    sid=d.session_id;$('#chat').replaceChildren();
    for(const t of d.turns){add('user',t.question);const m=add('bot',t.answer);if(t.refused)m.classList.add('refused');addSources(m,t.sources)}
    loadChats()}catch(e){alert(e.message)}}
function newChat(){sid=null;$('#chat').replaceChildren(el('div','hint',HINT));loadChats()}
$('#new').onclick=newChat;

/* ---------- conversation ---------- */
function add(cls,text){const m=el('div','msg '+cls,text);$('#chat').append(m);$('#chat').scrollTop=1e9;return m}
function addSources(m,sources){
  if(!sources||!sources.length)return;
  const d=el('details');d.append(el('summary','','Sources ('+sources.length+')'));
  for(const s of sources){d.append(el('div',s.cited?'cited':'',(s.cited?'\u2605 ':'')+'['+s.n+'] '+(s.filename||s.doc_id)+
    (s.page_start?', p.'+s.page_start+(s.page_end&&s.page_end!==s.page_start?'-'+s.page_end:''):'')+(s.section?'  \u00b7  '+s.section:'')))}
  m.append(d)}
async function send(){
  const q=$('#q').value.trim();if(!q||busy)return;busy=true;$('#send').disabled=true;$('#q').value='';
  if($('#chat .hint'))$('#chat').replaceChildren();
  if(!sid)sid=newId();add('user',q);const wait=add('bot','Thinking...');
  const ids=[...selected];
  try{
    const r=await api('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:q,session_id:sid,file_ids:ids.length?ids:null})});
    sid=r.session_id;wait.textContent=r.answer;if(r.refused)wait.classList.add('refused');addSources(wait,r.sources);loadChats()
  }catch(e){wait.remove();add('err',e.message)}
  busy=false;$('#send').disabled=false;$('#q').focus();$('#chat').scrollTop=1e9}
$('#send').onclick=send;$('#q').addEventListener('keydown',e=>{if(e.key==='Enter')send()});

/* ---------- files ---------- */
function renderFiles(){
  const box=$('#files');box.replaceChildren();
  if(!files.length)box.append(el('div','hint','No files yet.'));
  for(const f of files){
    const row=el('div','file');
    const cb=el('input');cb.type='checkbox';cb.disabled=f.status!=='ready';cb.checked=selected.has(f.file_id);
    cb.onchange=()=>{cb.checked?selected.add(f.file_id):selected.delete(f.file_id)};
    const n=el('span','n',f.filename);n.title=f.error||f.filename;
    const b=el('span','badge '+f.status,f.status==='ready'?f.chunks+' chunks':f.status);
    const x=el('button','x','\u00d7');x.title='Delete file';x.setAttribute('aria-label','Delete file');
    x.onclick=async()=>{try{await api('/files/'+f.file_id,{method:'DELETE'});selected.delete(f.file_id);loadFiles()}catch(e){alert(e.message)}};
    row.append(cb,n,b,x);box.append(row)}}
async function loadFiles(){
  if(!token)return;
  try{const prev=new Set(files.filter(f=>f.status==='ready').map(f=>f.file_id));
    files=await api('/files');
    for(const f of files)if(f.status==='ready'&&!prev.has(f.file_id))selected.add(f.file_id);
    renderFiles()}catch(e){return}
  if(files.some(f=>f.status==='queued'||f.status==='processing'))setTimeout(loadFiles,2000)}
$('#up').onclick=async()=>{
  const f=$('#pick').files[0];if(!f)return;$('#upmsg').textContent='Uploading...';
  const fd=new FormData();fd.append('file',f);
  try{await api('/files',{method:'POST',body:fd});$('#upmsg').textContent='Processing: PDFs can take a few minutes.';$('#pick').value='';loadFiles()}
  catch(e){$('#upmsg').textContent='';add('err','Upload failed: '+e.message)}};

/* ---------- start ---------- */
(async()=>{
  const h=await fetch('/health').then(r=>r.json()).catch(()=>({}));
  if(h.auth_required)$('#keybox').hidden=false;
  if(token){try{me=await api('/auth/me');return enter()}catch(e){}}
  leave('')})();
</script></body></html>"""


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run("engrag.api:app", host=host, port=port)


if __name__ == "__main__":
    main()

"""Accounts, login tokens and per-user chat history, stored in one SQLite file (data/users.db).

Standard library only: passwords are hashed with scrypt, and a login gives the browser a random opaque
token (only its SHA-256 is stored, so a leaked database cannot be used to log in). Logging out deletes the
token on the server, so it stops working immediately.

Tables: users, auth_tokens, chats (one row per conversation, owned by one user), messages (one row per
question/answer). A conversation is identified by (user_id, session_id), so two users can never see or
overwrite each other's chats, whatever session ids they send.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # a login lasts 30 days
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class UserExists(Exception):
    """The email or username is already registered."""


# ------------------------------------------------------------------ input checks (used by the API's request models)
def validate_email(v: str) -> str:
    v = (v or "").strip()
    if len(v) > 254 or not _EMAIL_RE.match(v):
        raise ValueError("enter a valid email address")
    return v.lower()


def validate_username(v: str) -> str:
    v = (v or "").strip()
    if not _USERNAME_RE.match(v):
        raise ValueError("use 3-32 letters, digits, '.', '_' or '-'")
    return v


def validate_password(v: str) -> str:
    if not 8 <= len(v or "") <= 128:
        raise ValueError("use 8-128 characters")
    return v


# ------------------------------------------------------------------ passwords
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_b64, dk_b64 = stored.split("$")
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
                            dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


_dummy: list[str] = []


def _dummy_hash() -> str:
    """Verified against when the user does not exist, so 'no such user' and 'wrong password' take equally long."""
    if not _dummy:
        _dummy.append(hash_password("not-a-real-password"))
    return _dummy[0]


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ login throttle
class LoginThrottle:
    """Blocks a (client address, account) pair after `max_failures` wrong passwords for `window` seconds.
    In memory only: it resets when the server restarts."""

    def __init__(self, max_failures: int = 5, window: int = 15 * 60):
        self.max_failures, self.window = max_failures, window
        self._fails: dict = {}
        self._lock = threading.Lock()

    def blocked_for(self, key) -> int:
        """Seconds until this key may try again (0 = allowed now)."""
        with self._lock:
            entry = self._fails.get(key)
            if not entry:
                return 0
            count, first = entry
            left = first + self.window - time.time()
            if left <= 0:
                del self._fails[key]
                return 0
            return int(left) + 1 if count >= self.max_failures else 0

    def fail(self, key) -> None:
        now = time.time()
        with self._lock:
            if len(self._fails) > 10_000:  # never grow without bound
                self._fails = {k: v for k, v in self._fails.items() if now - v[1] < self.window}
            count, first = self._fails.get(key, (0, now))
            if now - first >= self.window:
                count, first = 0, now
            self._fails[key] = (count + 1, first)

    def reset(self, key) -> None:
        with self._lock:
            self._fails.pop(key, None)


# ------------------------------------------------------------------ database
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    title      TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, session_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    turn       INTEGER NOT NULL,
    question   TEXT NOT NULL,
    standalone TEXT NOT NULL DEFAULT '',
    answer     TEXT NOT NULL,
    refused    INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT '',
    cited_docs TEXT NOT NULL DEFAULT '[]',
    sources    TEXT NOT NULL DEFAULT '[]',
    seconds    REAL,
    ts         REAL NOT NULL,
    FOREIGN KEY (user_id, session_id) REFERENCES chats(user_id, session_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chats_user    ON chats(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(user_id, session_id, id);
CREATE INDEX IF NOT EXISTS idx_tokens_user   ON auth_tokens(user_id);
"""


class UserStore:
    """All database access. Creates the file and tables on first use; every call opens its own short connection,
    so it is safe to use from FastAPI's worker threads."""

    def __init__(self, path):
        self.path = Path(path)
        self._ready = False
        self._init_lock = threading.Lock()

    def _init(self) -> None:
        if self._ready:
            return
        with self._init_lock:
            if self._ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10)
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(SCHEMA)
                conn.commit()
            finally:
                conn.close()
            self._ready = True

    @contextmanager
    def _db(self):
        self._init()
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")  # needed for ON DELETE CASCADE
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- users
    def create_user(self, email: str, username: str, password: str) -> dict:
        email = email.strip().lower()
        username = username.strip()
        pw_hash = hash_password(password)
        try:
            with self._db() as db:
                cur = db.execute(
                    "INSERT INTO users (email, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (email, username, pw_hash, time.time()))
                uid = cur.lastrowid
        except sqlite3.IntegrityError as e:
            raise UserExists("That email or username is already registered.") from e
        return {"id": uid, "email": email, "username": username}

    def authenticate(self, identifier: str, password: str) -> dict | None:
        """identifier is an email or a username (case-insensitive). Returns the user, or None."""
        ident = identifier.strip()
        with self._db() as db:
            row = db.execute("SELECT id, email, username, password_hash FROM users WHERE email = ? OR username = ?",
                             (ident, ident)).fetchone()
        ok = verify_password(password, row["password_hash"] if row else _dummy_hash())
        if row is None or not ok:
            return None
        return {"id": row["id"], "email": row["email"], "username": row["username"]}

    # ---------- login tokens
    def issue_token(self, user_id: int) -> str:
        token, now = secrets.token_urlsafe(32), time.time()
        with self._db() as db:
            db.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (now,))
            db.execute("INSERT INTO auth_tokens (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                       (_digest(token), user_id, now, now + TOKEN_TTL_SECONDS))
        return token

    def user_for_token(self, token: str) -> dict | None:
        with self._db() as db:
            row = db.execute(
                "SELECT u.id, u.email, u.username FROM auth_tokens t JOIN users u ON u.id = t.user_id "
                "WHERE t.token_hash = ? AND t.expires_at > ?", (_digest(token), time.time())).fetchone()
        return dict(row) if row else None

    def revoke_token(self, token: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (_digest(token),))

    # ---------- chat history
    def save_turn(self, user_id: int, session_id: str, *, question: str, answer: str, standalone: str = "",
                  refused: bool = False, status: str = "", cited_docs=(), sources=(), seconds: float | None = None) -> int:
        """Store one question/answer. Creates the conversation on its first turn (title = first question).
        Returns the turn number within the conversation."""
        now = time.time()
        title = " ".join(question.split())
        title = (title[:60] + "...") if len(title) > 60 else (title or session_id)
        with self._db() as db:
            db.execute(
                "INSERT INTO chats (user_id, session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (user_id, session_id) DO UPDATE SET updated_at = excluded.updated_at",
                (user_id, session_id, title, now, now))
            turn = db.execute("SELECT COUNT(*) FROM messages WHERE user_id = ? AND session_id = ?",
                              (user_id, session_id)).fetchone()[0] + 1
            db.execute(
                "INSERT INTO messages (user_id, session_id, turn, question, standalone, answer, refused, status, "
                "cited_docs, sources, seconds, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, session_id, turn, question, standalone, answer, int(refused), status,
                 json.dumps(list(cited_docs), ensure_ascii=False), json.dumps(list(sources), ensure_ascii=False),
                 seconds, now))
        return turn

    def list_chats(self, user_id: int) -> list[dict]:
        """Sidebar data for one user, newest first."""
        with self._db() as db:
            rows = db.execute(
                "SELECT c.session_id, c.title, c.updated_at, "
                "  (SELECT COUNT(*) FROM messages m WHERE m.user_id = c.user_id AND m.session_id = c.session_id) AS turns, "
                "  (SELECT m.answer FROM messages m WHERE m.user_id = c.user_id AND m.session_id = c.session_id "
                "   ORDER BY m.id DESC LIMIT 1) AS last_answer "
                "FROM chats c WHERE c.user_id = ? ORDER BY c.updated_at DESC, c.rowid DESC", (user_id,)).fetchall()
        return [{"session_id": r["session_id"], "title": r["title"],
                 "preview": " ".join((r["last_answer"] or "").split())[:100],
                 "turns": r["turns"], "updated_at": r["updated_at"]} for r in rows]

    def get_chat(self, user_id: int, session_id: str) -> dict | None:
        """The full conversation, or None if it does not exist *for this user*."""
        with self._db() as db:
            chat = db.execute("SELECT title FROM chats WHERE user_id = ? AND session_id = ?",
                              (user_id, session_id)).fetchone()
            if chat is None:
                return None
            rows = db.execute("SELECT * FROM messages WHERE user_id = ? AND session_id = ? ORDER BY id",
                              (user_id, session_id)).fetchall()
        turns = [{"turn": r["turn"], "question": r["question"], "standalone": r["standalone"], "answer": r["answer"],
                  "refused": bool(r["refused"]), "status": r["status"], "cited_docs": json.loads(r["cited_docs"]),
                  "sources": json.loads(r["sources"]), "seconds": r["seconds"], "ts": r["ts"]} for r in rows]
        return {"session_id": session_id, "title": chat["title"], "turns": turns}

    def delete_chat(self, user_id: int, session_id: str) -> bool:
        with self._db() as db:
            cur = db.execute("DELETE FROM chats WHERE user_id = ? AND session_id = ?", (user_id, session_id))
            return cur.rowcount > 0

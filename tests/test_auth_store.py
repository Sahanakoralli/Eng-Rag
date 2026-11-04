"""Tests for engrag.auth (no server, no models needed): python -m pytest tests/test_auth_store.py"""
import sqlite3
import time

import pytest

from engrag import auth
from engrag.auth import LoginThrottle, UserExists, UserStore


@pytest.fixture
def store(tmp_path):
    return UserStore(tmp_path / "data" / "users.db")  # the folder does not exist yet: the store must create it


def _signup(store, name="alice", email="Alice@Example.com", pw="correct horse"):
    return store.create_user(email, name, pw)


def test_signup_and_login_by_username_or_email(store):
    u = _signup(store)
    assert u["email"] == "alice@example.com"
    assert store.authenticate("alice", "correct horse")["id"] == u["id"]
    assert store.authenticate("ALICE@example.com", "correct horse")["id"] == u["id"]


def test_wrong_password_and_unknown_user_look_the_same(store):
    _signup(store)
    assert store.authenticate("alice", "nope nope nope") is None
    assert store.authenticate("ghost", "correct horse") is None


def test_duplicates_are_rejected_ignoring_case(store):
    _signup(store)
    with pytest.raises(UserExists):
        store.create_user("other@example.com", "ALICE", "another pass")
    with pytest.raises(UserExists):
        store.create_user("ALICE@example.com", "alice2", "another pass")


def test_password_is_not_stored_in_clear(store):
    _signup(store)
    raw = sqlite3.connect(store.path).execute("SELECT password_hash FROM users").fetchone()[0]
    assert "correct horse" not in raw and raw.startswith("scrypt$")


def test_token_lifecycle(store):
    u = _signup(store)
    t = store.issue_token(u["id"])
    assert store.user_for_token(t)["username"] == "alice"
    assert store.user_for_token(t + "x") is None
    # only a hash is stored
    assert t not in repr(sqlite3.connect(store.path).execute("SELECT * FROM auth_tokens").fetchall())
    store.revoke_token(t)
    assert store.user_for_token(t) is None


def test_expired_token_is_rejected(store):
    u = _signup(store)
    t = store.issue_token(u["id"])
    sqlite3.connect(store.path).execute("UPDATE auth_tokens SET expires_at = ?", (time.time() - 1,)).connection.commit()
    assert store.user_for_token(t) is None


def test_chat_history_roundtrip(store):
    u = _signup(store)["id"]
    src = [{"n": 1, "filename": "a.pdf", "citation": "a.pdf p.3", "cited": True}]
    assert store.save_turn(u, "s1", question="What is the span?", answer="12 m", standalone="span of the beam?",
                           cited_docs=["a.pdf"], sources=src, seconds=1.5) == 1
    assert store.save_turn(u, "s1", question="And the depth?", answer="0.4 m", refused=True) == 2
    chats = store.list_chats(u)
    assert [(c["session_id"], c["title"], c["turns"], c["preview"]) for c in chats] == [("s1", "What is the span?", 2, "0.4 m")]
    chat = store.get_chat(u, "s1")
    assert [t["question"] for t in chat["turns"]] == ["What is the span?", "And the depth?"]
    assert chat["turns"][0]["sources"] == src and chat["turns"][0]["cited_docs"] == ["a.pdf"]
    assert chat["turns"][1]["refused"] is True


def test_newest_chat_first_and_long_titles_are_cut(store):
    u = _signup(store)["id"]
    store.save_turn(u, "old", question="x" * 100, answer="a")
    time.sleep(0.01)
    store.save_turn(u, "new", question="second", answer="b")
    chats = store.list_chats(u)
    assert [c["session_id"] for c in chats] == ["new", "old"]
    assert chats[1]["title"] == "x" * 60 + "..."


def test_users_cannot_see_or_delete_each_others_chats(store):
    a = _signup(store)["id"]
    b = store.create_user("bob@example.com", "bob", "bobs password")["id"]
    store.save_turn(a, "same-id", question="alice question", answer="alice answer")
    store.save_turn(b, "same-id", question="bob question", answer="bob answer")  # same session id, different owner
    assert store.get_chat(a, "same-id")["turns"][0]["answer"] == "alice answer"
    assert store.get_chat(b, "same-id")["turns"][0]["answer"] == "bob answer"
    assert [c["title"] for c in store.list_chats(b)] == ["bob question"]
    assert store.delete_chat(b, "same-id") is True
    assert store.get_chat(a, "same-id") is not None  # alice's chat is untouched
    assert store.delete_chat(b, "same-id") is False
    assert store.get_chat(b, "nope") is None


def test_deleting_a_chat_deletes_its_messages(store):
    u = _signup(store)["id"]
    store.save_turn(u, "s", question="q", answer="a")
    store.delete_chat(u, "s")
    assert sqlite3.connect(store.path).execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_validators():
    assert auth.validate_email("  Bob@X.org ") == "bob@x.org"
    for bad in ("nope", "a@b", "a b@c.d", ""):
        with pytest.raises(ValueError):
            auth.validate_email(bad)
    assert auth.validate_username("bob_1") == "bob_1"
    for bad in ("ab", "has space", "a@b.c", "x" * 33):
        with pytest.raises(ValueError):
            auth.validate_username(bad)
    with pytest.raises(ValueError):
        auth.validate_password("short")


def test_login_throttle():
    t = LoginThrottle(max_failures=3, window=60)
    key = ("1.2.3.4", "alice")
    assert t.blocked_for(key) == 0
    for _ in range(3):
        t.fail(key)
    assert 0 < t.blocked_for(key) <= 61
    assert t.blocked_for(("1.2.3.4", "bob")) == 0  # other accounts are not affected
    t.reset(key)
    assert t.blocked_for(key) == 0

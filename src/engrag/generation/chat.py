"""Conversation memory: stores each question/answer on disk so follow-up questions work across restarts."""
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from engrag.config import settings
from engrag.generation.answer import Answer, answer


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", (name or "").strip()) or "default"


@dataclass
class Turn:
    question: str
    answer: str
    standalone: str = ""
    cited_docs: list = field(default_factory=list)
    sources: list = field(default_factory=list)  # [{"n", "citation", "section", "cited", "linked"}]
    refused: bool = False
    ts: float = 0.0
    status: str = ""


class Session:
    def __init__(self, name: str = "default", directory=None):
        self.name = safe_name(name)
        self.dir = Path(directory or settings.chats_dir)
        self.turns: list[Turn] = []
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.turns = [Turn(**t) for t in data.get("turns", [])]

    @property
    def path(self) -> Path:
        return self.dir / f"{self.name}.json"

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"name": self.name, "turns": [asdict(t) for t in self.turns]},
                                        indent=1, ensure_ascii=False), encoding="utf-8")

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)
        self.save()

    def recent(self, n: int) -> list[Turn]:
        return self.turns[-n:] if n > 0 else []

    def clear(self) -> None:
        self.turns = []
        self.path.unlink(missing_ok=True)


def list_sessions(directory=None) -> dict[str, int]:
    d = Path(directory or settings.chats_dir)
    out = {}
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            out[f.stem] = len(json.loads(f.read_text(encoding="utf-8")).get("turns", []))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def turn_from_answer(a: Answer) -> Turn:
    return Turn(
        question=a.question, answer=a.text, standalone=a.standalone, cited_docs=a.cited_docs, refused=a.refused, status=a.status,
        sources=[{"n": i, "citation": h.citation, "section": h.section_path, "cited": i in a.cited,
                  "linked": h.payload.get("linked_from", "")} for i, h in enumerate(a.hits, 1)],
        ts=time.time(),
    )


def chat_turn(question: str, retriever, session: Session, history_turns: int = 4, **answer_kwargs) -> Answer:
    """Answer a question in the context of the session, then remember it."""
    a = answer(question, retriever, history=session.recent(history_turns), **answer_kwargs)
    session.add(turn_from_answer(a))
    return a

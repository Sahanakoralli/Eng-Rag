"""Step 2: structure-aware chunking.

- Splits on Markdown headings and remembers the heading path of each chunk.
- Keeps tables and code blocks whole (splits an oversized table by rows, repeating the header).
- Falls back to recursive splitting (with overlap) for oversized paragraphs.
- Merges tiny sections into the next one (within the same chapter) so you don't get 5-word chunks.
- Prepends "Title > Section > Subsection" to every chunk so it is self-describing.
- Optional page tracking: if the Markdown contains page-break markers, each chunk gets page_start/page_end.
"""
import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
TABLE_SEP = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")
CHARS_PER_TOKEN = 4  # rough estimate; good enough for sizing
CHUNKER_VERSION = 3  # bump when chunking output changes: ingest then rebuilds chunks from the saved Markdown
_SEP_CELL = re.compile(r"^:?-+:?$")

Line = tuple  # (page | None, text)
Block = tuple  # (kind, text, page | None)


@dataclass
class Chunk:
    text: str
    section_path: str
    kind: str  # text | table | code | mixed
    index: int = 0
    page_start: int | None = None
    page_end: int | None = None


def est_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _to_lines(md: str, page_break: str | None) -> list[Line]:
    """Split into (page, line). Page-break marker lines are consumed and advance the page counter."""
    page = 1 if page_break else None
    out: list[Line] = []
    for line in md.splitlines():
        if page_break and line.strip() == page_break:
            page += 1
        else:
            out.append((page, line))
    return out


def _sections(lines: list[Line]):
    """Return [(path, heading (page,text) | None, body_lines)]. Ignores '#' inside code fences."""
    stack: list[str] = []
    in_fence = False
    cur = ([], None, [])
    out = []
    for page, line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(line)
        if m:
            out.append(cur)
            level = len(m.group(1))
            stack = stack[: level - 1] + [m.group(2)]
            cur = (list(stack), (page, line), [])
        else:
            cur[2].append((page, line))
    out.append(cur)
    return out


def _blocks(lines: list[Line]) -> list[Block]:
    """Group lines into (kind, text, page) blocks: paragraphs, tables, code."""
    blocks: list[Block] = []
    buf: list[Line] = []

    def flush():
        if buf:
            blocks.append(("text", "\n".join(t for _, t in buf), buf[0][0]))
            buf.clear()

    texts = [t for _, t in lines]
    i = 0
    while i < len(lines):
        page, line = lines[i]
        if FENCE.match(line):
            flush()
            j = i + 1
            while j < len(lines) and not FENCE.match(texts[j]):
                j += 1
            blocks.append(("code", "\n".join(texts[i : j + 1]), page))
            i = j + 1
        elif line.lstrip().startswith("|"):
            flush()
            j = i
            while j < len(lines) and texts[j].lstrip().startswith("|"):
                j += 1
            blocks.append(("table", _compact_table("\n".join(texts[i:j])), page))
            i = j
        elif not line.strip():
            flush()
            i += 1
        else:
            buf.append((page, line))
            i += 1
    flush()
    return blocks


def _compact_table(text: str) -> str:
    """Docling pads every table cell with spaces to the column width. Remove the padding (same content,
    far fewer tokens), so table chunks hold more real information and cost less in the LLM prompt."""
    out = []
    for line in text.splitlines():
        row = line.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|") and not row.endswith("\\|"):
            row = row[:-1]
        cells = [re.sub(r"\s{2,}", " ", c.strip()) for c in re.split(r"(?<!\\)\|", row)]  # also collapse wide gaps inside a cell
        if cells and all(_SEP_CELL.match(c) for c in cells):  # the |---|---| line
            cells = [(":" if c.startswith(":") else "") + "---" + (":" if c.endswith(":") and len(c) > 1 else "") for c in cells]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _split_table(text: str, max_chars: int) -> list[str]:
    lines = text.splitlines()
    header = lines[:2] if len(lines) >= 2 and TABLE_SEP.match(lines[1]) else []
    rows = lines[len(header) :]
    pieces, cur, size = [], [], 0
    head_len = sum(len(h) + 1 for h in header)
    for row in rows:
        if cur and head_len + size + len(row) + 1 > max_chars:
            pieces.append("\n".join(header + cur))
            cur, size = [], 0
        cur.append(row)
        size += len(row) + 1
    if cur:
        pieces.append("\n".join(header + cur))
    return pieces


def _split_code(text: str, max_chars: int) -> list[str]:
    pieces, cur, size = [], [], 0
    for line in text.splitlines():
        if cur and size + len(line) + 1 > max_chars:
            pieces.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        pieces.append("\n".join(cur))
    return pieces


def _pack(blocks: list[Block], max_chars: int, overlap_chars: int):
    """Greedily pack blocks into chunks <= max_chars. Returns [(kind, text, page_start, page_end)]."""
    out: list = []
    cur: list[Block] = []
    size = 0

    def flush():
        nonlocal cur, size
        if cur:
            kinds = {k for k, _, _ in cur}
            kind = kinds.pop() if len(kinds) == 1 else "mixed"
            pages = [p for _, _, p in cur if p is not None]
            out.append((kind, "\n\n".join(t for _, t, _ in cur),
                        pages[0] if pages else None, pages[-1] if pages else None))
        cur, size = [], 0

    splitter = RecursiveCharacterTextSplitter(chunk_size=max_chars, chunk_overlap=overlap_chars)
    for kind, text, page in blocks:
        if len(text) > max_chars:
            flush()
            if kind == "table":
                pieces = _split_table(text, max_chars)
            elif kind == "code":
                pieces = _split_code(text, max_chars)
            else:
                pieces = splitter.split_text(text)
            out.extend((kind, p, page, page) for p in pieces)
            continue
        if cur and size + len(text) + 2 > max_chars:
            flush()
        cur.append((kind, text, page))
        size += len(text) + 2
    flush()
    return out


def chunk_markdown(
    md: str,
    title: str,
    max_tokens: int = 600,
    overlap_tokens: int = 80,
    min_tokens: int = 60,
    page_break: str | None = None,
) -> list[Chunk]:
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    min_chars = min_tokens * CHARS_PER_TOKEN

    chunks: list[Chunk] = []
    carry: list[Block] = []
    carry_path: list[str] = []
    sections = _sections(_to_lines(md, page_break))

    def emit(blocks, path):
        trail = path[1:] if path and path[0].strip().lower() == title.strip().lower() else path
        prefix = " > ".join([title] + trail)
        for kind, text, p0, p1 in _pack(blocks, max_chars, overlap_chars):
            chunks.append(Chunk(text=f"{prefix}\n\n{text}", section_path=" > ".join(path),
                                kind=kind, page_start=p0, page_end=p1))

    for n, (path, heading, body) in enumerate(sections):
        body_blocks = _blocks(body)
        if not body_blocks:
            continue  # heading-only section: its title already lives in child sections' paths
        if carry and carry_path[:2] != path[:2]:
            emit(carry, carry_path)  # never merge across chapters: labels would mislead
            carry = []
        # A section's own heading is already the end of the chunk's context line; repeat it in the body only
        # when this section follows merged-in small sections, where it separates the parts.
        head = [("text", heading[1], heading[0])] if heading and carry else []
        blocks = carry + head + body_blocks
        first_path = carry_path if carry else path
        carry = []
        total = sum(len(t) for _, t, _ in blocks)
        if total < min_chars and n < len(sections) - 1:
            carry, carry_path = blocks, first_path  # too small: merge into the next section
            continue
        emit(blocks, first_path)
    if carry:
        emit(carry, carry_path)

    for i, c in enumerate(chunks):
        c.index = i
    return chunks

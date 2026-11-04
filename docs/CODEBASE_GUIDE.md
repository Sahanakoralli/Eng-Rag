# eng-rag codebase guide

How the project works and where to change things. File and function names below are real; open the file
next to this guide while reading.

## 1. The big picture

```
 DOCUMENTS            INGESTION (once per file)                     INDEX                 QUESTION TIME
 pdf/docx/...  --->  parse -> chunk -> embed -> store   --->   Qdrant (local)   --->   rewrite -> search -> link tables
                     parse.py chunk.py embed.py index.py        dense + BM25 +           -> LLM answer with citations
                     (pipeline.py runs the steps)               metadata                 retrieval/ + generation/
                                                                                         (api.py / cli.py are the front doors)
```

Two ideas hold everything together:

1. **Everything is a *chunk* with metadata.** A document becomes many chunks (about 600 tokens). Each chunk is stored with a
   meaning vector (dense), a keyword vector (BM25) and a payload: text, document id, section path, page range, kind.
2. **Search is hybrid.** Meaning search and keyword search both run; their rankings are merged (RRF). Then the top chunks go to
   an LLM that must answer from them, with numbered citations.

## 2. Folder map

```
src/engrag/
  config.py              ALL settings, read from .env (one frozen `settings` object)
  cli.py                 every command: ingest, search, ask, chat, evaluate, serve, find-text, ...
  api.py                 web app + REST API (FastAPI) incl. the chat page (one file)
  ingestion/
    parse.py             file -> Markdown (Docling for PDF/DOCX/PPTX/HTML; plain read for MD/TXT); tables/figures extraction
    chunk.py             Markdown -> chunks (the chunking strategy lives here)
    embed.py             Embedder: dense + sparse vectors, local models (fastembed)
    index.py             Qdrant helpers: create collection, upsert, delete a document
    pipeline.py          ingest(): ties the steps together; incremental; progress file (manifest)
    figures.py           caption cache (never pay twice for the same image)
  retrieval/
    search.py            Retriever (hybrid/dense/sparse, filters, optional rerank), Hit, Reranker
    expand.py            "see Table 1" -> also fetch Table 1 from the same document
  generation/
    llm.py               ONE function `complete()` for all LLM providers; `describe_image()` for figures
    answer.py            prompt rules (SYSTEM), follow-up rewrite, answer(), citation parsing, refusal detection
    chat.py              conversation memory: Session (JSON files), Turn, chat_turn()
  evaluation/
    metrics.py           retrieval scores (hit@k, recall@k, MRR) from eval/*.jsonl
    answers.py           LLM-graded answer quality + citation checks
    health.py            index health report (used by `inspect`)
    unidoc.py            downloads the UniDoc-Bench dataset + builds the eval file (dataset-specific; delete if you use your own data)
tests/                   one test file per module; conftest.py holds the fake models
scripts/                 setup.ps1 / setup.sh / verify_setup.py
docs/                    this guide
data/                    created at run time (see section 6)
```

## 3. Ingestion: how a file becomes searchable (`ingestion/`)

`pipeline.ingest(raw_dir, ...)` loops over files and, for each new or changed one:

1. **Skip check.** SHA-256 of the file (+ its sidecar metadata) is compared with `manifest.json`. Unchanged -> skipped.
2. **`parse.parse_file(path)`** returns `Parsed(markdown, pages, n_tables, ...)`.
   PDFs go through Docling (layout, tables, optional OCR). Page breaks are written into the Markdown as the marker
   `PAGE_BREAK` so the chunker can count pages. With `--images`, figures are saved and described by a vision model; the
   description replaces the picture as `[Image N: ...]`.
3. **`chunk.chunk_markdown(md, title, ...)`** (the strategy; details below).
4. **`Embedder.dense/sparse`** turn chunk texts into vectors (batches of 64).
5. **`index.delete_doc` + `index.upsert_chunks`** replace the document's old chunks with the new ones.
6. **Checkpoint.** The manifest is saved after *every* document, so a stopped run resumes where it left off.

A failing file is recorded in the result (`stats["failed"]`) and never stops the run.

**Re-chunking without re-parsing.** Parsing PDFs is the slow part (hours); chunking and embedding are fast (minutes). The parsed
Markdown is saved in `data/processed/`, so when only chunk/embedding settings or `CHUNKER_VERSION` changed (not `images`/`ocr`),
`ingest` rebuilds every chunk from the saved Markdown automatically. `ingest --rechunk` does it on demand. A file that changed
since it was parsed, or has no saved Markdown, is parsed again.

### The chunking strategy (`chunk.py`)

Structure-aware chunking, with recursive splitting only as a fallback:

- Lines are split into **sections** at Markdown headings (`#` inside code fences is ignored). Each chunk remembers its heading path.
- Section bodies are split into **blocks**: paragraphs, tables (consecutive `|` lines), code fences.
- `_pack` fills chunks up to `CHUNK_SIZE` tokens (estimate = characters / 4). Tables and code stay whole; an oversized table is
  split by rows with the header repeated (`_split_table`); an oversized paragraph is split recursively with `CHUNK_OVERLAP`.
- Sections under 60 tokens are merged into the next section, **only within the same chapter** (so labels never mislead).
  Heading-only sections are dropped (their title is already in their children's paths).
- Every chunk text starts with `Title > Section > Subsection`. A section's own heading is not repeated in the body
  (only when small sections were merged, where it separates the parts). The title is the document's first heading of any level
  (`pipeline._title`), else the file name.
- Tables are compacted (`_compact_table`): Docling pads every cell with spaces to the column width; the padding is removed
  (about 4x fewer tokens, same content).
- Before chunking, `parse.clean_markdown` restores characters Docling escaped (`&amp;` -> `&`, `\_` -> `_`).
- `CHUNKER_VERSION` (in `chunk.py`) must be bumped whenever chunking output changes, so existing indexes are rebuilt.
- Page numbers: `_to_lines` counts page-break markers; each block knows its page; a chunk gets `page_start`/`page_end`.

### What is stored per chunk (the payload)

`text`, `doc_id`, `source` (file name), `title`, `doc_type`, `section_path`, `chunk_kind` (text/table/mixed/code),
`chunk_index` (position inside the document: neighbours are +/-1), `tokens`, `page_start`/`page_end` (PDFs), plus every key from
the optional sidecar file `<file>.meta.json` (e.g. `{"product": "XP-200"}`; uploads add `file_id`, `filename`).
Vectors: a dense `dense` vector (cosine) and a sparse `bm25` vector (IDF). Point ids are deterministic (`uuid5(doc_id::i)`).

## 4. Question time (`retrieval/` and `generation/`)

`answer.answer(question, retriever, history=...)` is the core. In order:

1. **Rewrite** (only if there is chat history): `rewrite_question` turns "who proposed the second one?" into a standalone question.
2. **Search**: `Retriever.search(query, k, mode, filters)`.
   - `hybrid`: dense and BM25 each fetch 50 candidates; Qdrant merges them with RRF (`_candidates`).
   - `build_filter` turns `{"product": "XP-200"}` or `{"file_id": [..]}` into a Qdrant filter.
   - `rerank=True` re-scores with a cross-encoder (off by default: slow, and it was not better in our tests).
3. **Link tables**: `expand_references` looks for "Table N" in the top chunks and fetches the chunk holding that table from the same document (matched by its caption line; neighbours +/-1 are checked).
4. **Prompt**: `build_prompt` = numbered SOURCES + (optional) CONVERSATION + QUESTION. Rules for the model are in `SYSTEM`:
   cite every claim, copy numbers exactly, never use outside knowledge, and start the reply with a status line:
   `STATUS: ANSWERED`, `STATUS: PARTIAL` or `STATUS: NOT_FOUND`.
5. **LLM call**: `llm.complete(prompt, system=...)`.
6. **Post-process**: `parse_status` reads and removes the status line. `NOT_FOUND` -> `Answer.refused` (the text is made to start
   with the `REFUSAL` sentence, and no citations are kept); `ANSWERED`/`PARTIAL` -> `[n]` citations are parsed into
   `Answer.cited` and `Answer.cited_docs`. If the model forgets the status line, an answer that starts with the `REFUSAL`
   sentence is treated as a refusal (older behaviour). Why a status line: judging by the opening sentence made the model answer
   *and* say "I could not find this" in the same reply, and the citation was thrown away.

`Answer` is the result object: `text`, `hits`, `cited`, `refused`, `status`, `standalone` (what was actually searched).

### The LLM layer (`llm.py`)

`complete()` and `describe_image()` switch on `LLM_PROVIDER`: `openai`, `anthropic`, `gemini`, `nvidia`. Provider quirks live here
and nowhere else: Gemini retries rate limits and picks a thinking setting per model family; NVIDIA turns reasoning off by default
(reasoning tokens eat the output limit) and strips leaked `<think>` text. `_retry` handles 429/5xx with visible waits.

## 5. Conversations, web app and API

- **`chat.py`**: `Session(name)` stores `Turn`s in `data/chats/<name>.json`. `chat_turn()` = answer with the last N turns as history, then save.
- **`api.py`**: `create_app()` builds the FastAPI app. Uploads go to `data/uploads/<file_id>/`, are indexed by a background worker thread
  into a *separate* Qdrant collection (`uploads`, own manifest and processed folder) and are queried with a `file_id` filter.
  Every `/ask` is stored in the session file and appended to `data/qa_log.jsonl`. `LockedClient` serialises Qdrant calls
  (local mode is not thread-safe); slow work (parsing, embedding, LLM) runs outside that lock.
  The chat page is the `CHAT_PAGE` string at the bottom (plain HTML/JS, no build step; it only uses `textContent`, never `innerHTML`).
- **`cli.py`**: thin wrappers; real logic is in the modules above. Commands: `ingest`, `get-data`, `inspect`, `search`, `ask`, `chat`,
  `chat-history`, `evaluate`, `evaluate-answers`, `find-text`, `sample-questions`, `list-models`, `serve`, `status`, `reset`.

## 6. Where data lives (all under `data/`, created at run time)

| Path | Contents |
|---|---|
| `raw/` | your source documents (`raw/construction/...`) |
| `processed/` | the Markdown the parser produced (open these to see what was extracted); `captions.json`; `<doc>_assets/` tables/figures |
| `qdrant/` | the vector database (collections `eng_docs` and `uploads`) |
| `manifest.json` | progress file: per document hash + chunk count + the settings signature |
| `chats/` | one JSON per conversation |
| `qa_log.jsonl` | every question and answer asked through the API |
| `uploads/`, `uploads_manifest.json`, `uploads_processed/` | the web app's own files and bookkeeping |
| `../eval/` | `*_eval.jsonl` question files and `results/` (failures, graded answers) |

A **document id** (`doc_id`) is the path relative to its folder, with forward slashes (`construction/0004245.pdf`; uploads:
`<file_id>/<name>`). Eval files refer to documents by this id, so renaming files breaks evaluation.

## 7. Settings (`config.py` / `.env`)

| Variable | Effect | Needs re-index? |
|---|---|---|
| `LLM_PROVIDER`, `LLM_MODEL`, `*_API_KEY` | which LLM answers (blank model = provider default) | no |
| `VISION_MODEL` | model for figure captions | only with `--images` |
| `EMBEDDING_MODEL`, `SPARSE_MODEL` | search vectors | **yes** (automatic) |
| `CHUNK_SIZE`, `CHUNK_OVERLAP` | chunk length | **yes** (automatic) |
| `RERANKER_MODEL` | cross-encoder | no |
| `QDRANT_PATH`, `COLLECTION_NAME` | where/what the main index is | - |
| `UPLOAD_OCR`, `MAX_UPLOAD_MB`, `API_KEY` | web app behaviour/security | - |
| `NVIDIA_THINKING` | NVIDIA reasoning on/off | no |

`settings` is a frozen dataclass read once at import. Tests change it with `object.__setattr__(settings, "name", value)`.
The manifest stores a signature of embedding model, chunk size, OCR and images; if it differs, `ingest` re-indexes everything.

## 8. "I want to change ..." recipes

| Goal | Where | Then |
|---|---|---|
| Bigger/smaller chunks | `.env` `CHUNK_SIZE`/`CHUNK_OVERLAP` | `ingest` (rebuilds from the saved Markdown, minutes) |
| Different chunking logic | `chunk.py` (`chunk_markdown`, `_pack`, `_sections`); **bump `CHUNKER_VERSION`** | `ingest` (auto re-chunk); `tests/test_chunk.py`; check with `inspect`, `find-text`, `evaluate-answers` |
| Support a new file type | `parse.py`: add to `SUPPORTED_EXTS` and a branch in `parse_file`; add to the file picker `accept=` in `CHAT_PAGE` | add a test |
| Better/other embedding model | `.env` `EMBEDDING_MODEL` (any fastembed model) | `ingest`, then `evaluate --compare` |
| Add a metadata field for filtering | write it in `<file>.meta.json` | filter with `search --filter key=value` or `filters` in `/ask` |
| Change how the model answers (tone, strictness, format) | `SYSTEM` in `answer.py` | run `evaluate-answers` before and after; watch `refused` and `incorrect` |
| See what the model was given | `ask "..." --debug` | |
| More/less context for the LLM | `k` (`--k`, API `k`) and `max_extra` in `expand_references` | |
| Follow other references ("Figure 3", "Section 7") | `retrieval/expand.py` (`MENTION`, `find_table_chunks`) | add tests |
| Use a different LLM provider | `llm.py`: add a branch in `complete()` and `describe_image()`; add to `DEFAULT_MODELS` in `config.py`; key name in `scripts/verify_setup.py` | mock the client in `tests/test_llm.py` |
| New API endpoint | inside `create_app()` in `api.py`; add `auth` dependency | `tests/test_api.py` (TestClient) |
| Production index (many users/documents) | `index.get_client`: use `QdrantClient(url=...)`; create payload indexes on `doc_id`, `file_id` | `LockedClient` can stay |
| Your own eval set | JSONL lines: `id, question, reference_answer, gold_docs` (doc ids as in section 6) | `evaluate`, `evaluate-answers --file ...` |
| 500-page documents (not built yet) | `parse.py` (convert in page batches with checkpoints), `chunk.py` (use PDF outline for headings, strip repeated headers/footers), `search.py` (parent-child: search small chunks, return the section), `pipeline.py` | build a long-document eval set first |

## 9. Tests

`pytest` runs everything (about 160 tests, a few seconds, no network, no model downloads).
`tests/conftest.py` provides `FakeEmbedder` (hash-based vectors) and `FakeReranker`; LLM calls are replaced with
`monkeypatch.setattr(ans.llm, "complete", ...)`; Qdrant runs in memory (`QdrantClient(":memory:")`).
The real parser (Docling) and real models are never used in tests, so a change that touches them must be tried on a real PDF.
Rule of thumb: change code, add or adjust the test in the matching `test_*.py`, run `pytest`.

## 10. Things to know before changing code

- **One process owns the local index.** While `serve` runs (or any command), other commands cannot open `data/qdrant`.
- **Evaluation is document-level**: it checks that the right document was found, not the right passage or a correct answer.
  `evaluate-answers` checks answers (LLM grader: read the failures yourself).
- **Prompt changes need measuring.** More permissive answering means fewer refusals but more risk of unsupported claims.
- **Heading quality drives chunk quality.** If Docling finds few headings, chunks fall back to paragraph packing and section labels are poor.
- **Page numbers exist for PDFs only.** Captions assume the parser keeps figures and placeholders in the same order (it drops them if counts differ).
- **Table linking depends on caption format** (`Table 1. ...` at the start of a line).
- **Free LLM tiers**: rate limits (retried automatically), retired model names (use `list-models`), NVIDIA reasoning tokens.
- **Chat history is plain text on disk** (`data/chats`, `data/qa_log.jsonl`): treat it as sensitive if your documents are.

## 11. Debugging cheat sheet

| Symptom | First command |
|---|---|
| "Is my document in the index at all?" | `status`, `inspect` |
| "Is this sentence/table in the index, and how was it chunked?" | `find-text "text" --doc 123` |
| "Why was the answer refused or wrong?" | `ask "..." --debug` (shows exactly what the model saw). A refusal is correct when the sources really do not cover the question |
| "Which question can I test with?" | `sample-questions` (questions the corpus is known to answer, with the expected answer) |
| "Is retrieval the problem?" | `search "..." --k 10` (no LLM involved) |
| "Did my change help overall?" | `evaluate --compare`, `evaluate-answers --limit 10 --pause 3` |
| "Parsing looks wrong" | open `data/processed/<doc>.md` |
| "Model not found / rate limited" | `list-models`; check the waiting messages |

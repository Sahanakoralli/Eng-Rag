New to the code? Read [docs/CODEBASE_GUIDE.md](docs/CODEBASE_GUIDE.md): how it works and where to change things.

RAG pipeline for engineering documents (manuals, specs, standards, code docs).

## Stack

| Layer | Tool | Why |
|---|---|---|
| Parsing | Docling | Layout-aware; keeps tables and headings |
| Chunking | langchain-text-splitters | Markdown-header (structure-aware) + recursive fallback |
| Vector DB | Qdrant (local mode) | Metadata filtering + hybrid search, no Docker needed |
| Embeddings / sparse / rerank | fastembed | Runs locally on CPU, no API cost |
| LLM | OpenAI or Anthropic API (switch in .env) | Answers and contextual chunk blurbs |

## Requirements

- Python 3.10+ (3.11 or 3.12 recommended)
- Git
- ~3 GB free disk (CPU PyTorch + Docling models); ~10 GB if you use GPU PyTorch
- An OpenAI API key (or Anthropic)

## Setup

**Windows (PowerShell):**
```powershell
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
```
If scripts are blocked: `Set-ExecutionPolicy -Scope Process Bypass`

**macOS / Linux:**
```bash
bash scripts/setup.sh
source .venv/bin/activate
```

Options: `--no-parsing` / `-NoParsing` skips Docling (add it later with
`pip install -r requirements-parsing.txt`). `--gpu` / `-Gpu` keeps the default
CUDA PyTorch instead of the CPU-only build.

Then open `.env` and set `OPENAI_API_KEY` (and `LLM_MODEL` if needed).

Re-check anytime: `python scripts/verify_setup.py --models`
Run tests: `pytest`

## Layout

```
data/raw/          put source documents here (PDF, DOCX, ...)
data/processed/    parsed markdown output
data/qdrant/       local vector DB (created automatically)
src/engrag/
  ingestion/       parse -> chunk -> metadata -> index
  retrieval/       hybrid search, filters, reranking
  generation/      prompts, answer + citations
  evaluation/      recall@k and answer checks
eval/              your question/answer test set
scripts/           setup and verification
tests/
```

## Get a dataset (optional)

```
python -m engrag.cli get-data --domain construction --max-docs 200
```
Downloads UniDoc-Bench PDFs for one domain from Hugging Face (about 0.5-0.9 GB, needs ~3 GB temporary disk),
copies a subset to `data/raw/<domain>/`, and writes `eval/<domain>_eval.jsonl` (question, reference answer,
gold document). Domains: commerce_manufacturing, construction, energy (also crm, education, finance, healthcare, legal).
**License: CC BY-NC 4.0, non-commercial use only.**

## Ingest documents

1. Put files in `data/raw/` (PDF, DOCX, PPTX, HTML, MD, TXT; subfolders are fine).
   A sample manual is included so you can test immediately.
2. Optional metadata: next to `manual.pdf` add `manual.pdf.meta.json`, e.g.
   `{"product": "XP-200", "version": "3.1"}`. Every key is stored on every chunk and can be used as a filter.
3. Run:
```
python -m engrag.cli ingest          # new/changed files only
python -m engrag.cli ingest --force  # re-index everything
python -m engrag.cli status          # what is indexed
python -m engrag.cli reset           # wipe the index (keeps your documents)
```
### Tables, figures and page numbers

- **Tables** are kept as whole Markdown tables inside chunks (a huge table is split by rows with its header repeated).
- **Page numbers** are stored on every chunk of a PDF (`page_start`, `page_end`) for citations.
- **Figures:** `python -m engrag.cli ingest --images` saves figures to `data/processed/<doc>_assets/images/`,
  tables to `.../tables/*.csv`, and has a vision model write a short description of each figure, which is
  inserted into the text as `[Image N: ...]` so figures become searchable. This costs API credits
  (small logos/icons are skipped; max `MAX_IMAGES_PER_DOC` per document; captions are cached in
  `data/processed/captions.json`, so you never pay twice). Turning `--images` on or off re-indexes everything.

### Long runs

```
python -m engrag.cli ingest --limit 20     # process 20 new files, then stop; run again to continue
python -m engrag.cli ingest --no-ocr       # faster; fine for PDFs that already contain real text
python -m engrag.cli ingest --rechunk      # rebuild all chunks from the saved Markdown (minutes, no re-parsing)
```
Progress is saved after every document, so `Ctrl+C` and re-running resumes where it stopped.
Use `--ocr` (default) for scanned PDFs. Toggling OCR re-indexes everything.

Re-running is safe: unchanged files are skipped, edited files are replaced, deleted files are removed,
and a bad file is reported without stopping the run. Parsed Markdown is saved to `data/processed/` so you can
inspect what the parser extracted. The first ingest downloads the local models (a few minutes).

## Chat with an uploaded file (web app + API)

```
python -m engrag.cli serve            # then open http://127.0.0.1:8000
```
Upload a PDF/DOCX/PPTX/HTML/MD/TXT in the browser, wait for **ready** (PDFs take a while), then ask questions; follow-ups
work. Uploads are indexed in their own collection (`uploads`), so they never touch your main index.
Every question and answer is stored in `data/chats/<session>.json` and appended to `data/qa_log.jsonl`.
While the server runs it holds the index, so other `engrag` commands cannot open it: stop the server first.

| Endpoint | What it does |
|---|---|
| `POST /files` (multipart `file`) | upload; returns `file_id`, status `queued` -> `processing` -> `ready` / `failed` |
| `GET /files`, `GET /files/{id}`, `DELETE /files/{id}` | list, check status, remove (also from the index) |
| `POST /ask` | `{"question", "session_id"?, "file_ids"?, "k"?, "include_text"?}` -> answer, cited sources, `session_id` |
| `POST /search` | retrieval only (no LLM) |
| `GET /sessions`, `GET /sessions/{id}`, `DELETE /sessions/{id}` | stored conversations |
| `GET /log?limit=50` | latest questions and answers |
| `GET /health`, `GET /docs` | status; interactive API docs |

```python
import requests
B = "http://127.0.0.1:8000"
fid = requests.post(f"{B}/files", files={"file": open("manual.pdf", "rb")}).json()["file_id"]
# ... poll GET /files/{fid} until status == "ready" ...
r = requests.post(f"{B}/ask", json={"question": "What is the torque for M10 bolts?", "file_ids": [fid]}).json()
r2 = requests.post(f"{B}/ask", json={"question": "and for M12?", "session_id": r["session_id"], "file_ids": [fid]}).json()
```
Set `API_KEY` in `.env` to require an `X-API-Key` header. Keep the default host (127.0.0.1) unless you set a key.

## React chat UI (`frontend/`)

A ChatGPT-style interface: chat history sidebar (grouped by day, searchable, deletable), document upload
(button, multi-file, or drag-and-drop), per-document selection, markdown answers with clickable `[1]` citations and a
source list. Old chats are re-opened from `data/chats/`, so history survives restarts.

**Use it (one server, one port).** Needs Node 18+ once, to build the UI:
```
cd frontend && npm install && npm run build && cd ..
python -m engrag.cli serve            # open http://127.0.0.1:8000
```
If `frontend/dist` does not exist, `serve` falls back to the old built-in page.

**Develop it (hot reload).** Run `python -m engrag.cli serve` in one terminal and `cd frontend && npm run dev` in another,
then open http://localhost:5173 (API calls are proxied to port 8000).

`GET /history` (added for the sidebar) lists chats with title, preview and last-updated time.

## Verify and evaluate

```
python -m engrag.cli inspect                          # index health: sizes, page numbers, suspicious docs, random samples
python -m engrag.cli search "torque for M10 casing bolt" --k 5
python -m engrag.cli search "safety" --filter doc_type=pdf   # metadata filter (repeatable)
python -m engrag.cli get-data --eval-only             # build eval/construction_eval.jsonl for the docs you indexed
python -m engrag.cli evaluate --compare               # dense vs BM25 vs hybrid vs hybrid+rerank
```
`evaluate` asks each question and checks whether a chunk from the correct document is in the top results
(hit@k = found in top k; MRR = how high the first correct chunk ranked). Questions it misses are saved to
`eval/results/*_failures.json`: read those to see what to fix.

## Ask questions (needs an LLM API key)

Set `LLM_PROVIDER` to `openai`, `gemini`, `nvidia` or `anthropic` in `.env`, plus the matching key (`OPENAI_API_KEY`,
`GEMINI_API_KEY`, `NVIDIA_API_KEY`, `ANTHROPIC_API_KEY`).

NVIDIA (build.nvidia.com, key starts with `nvapi-`): `LLM_PROVIDER=nvidia`, leave `LLM_MODEL` blank for
`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`. It is a *reasoning* model: its hidden thinking uses up the token limit, so
thinking is **off** by default (`NVIDIA_THINKING=0`); set `NVIDIA_THINKING=1` to let it think first (slower, more tokens).
No LangChain is needed: the project calls NVIDIA's OpenAI-compatible endpoint. `list-models` shows the model ids your key can use. Leave `LLM_MODEL` blank for the default (Gemini: `gemini-3.8-flash`; Google retires old models, so if you see a 404 "model not found" run `python -m engrag.cli list-models` and set `LLM_MODEL` to a name from that list). Gemini has a free tier
(low rate limits, retried automatically; on the free tier Google may use your prompts to improve its products,
so do not send confidential documents).

```
python -m engrag.cli ask "What is the torque for the M10 casing bolt?"
python -m engrag.cli ask "What PPE is required?" --filter doc_type=pdf
python -m engrag.cli evaluate-answers --limit 10      # answers the eval questions and grades them (a few cents)
python -m engrag.cli evaluate-answers --limit 10 --pause 6   # free API tiers: wait 6s between questions
```
`ask --debug` prints the full text of every source the model was given (use it when an answer is refused or wrong).
`ask` retrieves with hybrid search (reranking is off by default: it was 30x slower and not better on our test),
sends the top 6 chunks to the LLM, and prints the answer with numbered citations (document, page, section).
If the sources do not contain the answer it says so instead of guessing.
### Follow-up questions

```
python -m engrag.cli ask "Which techniques evaluate slope in Grid-DTMs?" --session demo
python -m engrag.cli ask "Who proposed the second one?" --session demo      # remembers the first question
python -m engrag.cli chat --session demo                                    # interactive; /new /history /sources /quit
python -m engrag.cli chat-history demo                                      # show a saved conversation (--clear to delete)
```
With `--session`, each question and answer is saved in `data/chats/<name>.json`. A follow-up is first rewritten into a
standalone question using the last few turns ("who proposed the second one?" becomes "Who proposed
Neighborhood?"), and that rewritten question is what gets searched. The answer must still be supported by the
newly retrieved sources, not by earlier answers. Without `--session`, `ask` is stateless.

When a retrieved chunk mentions "Table N", `ask` also fetches that table from the same document (tables match questions
poorly, so they often rank below the prose that mentions them). Switch off with `--no-expand` to compare.
`find-text "Table 1" --doc 2692012` shows whether a given text or table is actually in the index.
`evaluate-answers` compares generated answers with the reference answers using an LLM grader, and also reports
whether the system retrieved and cited the correct document. Read the `partial`/`incorrect` ones yourself.

## Next steps

1. ~~Ingestion~~ (done)
2. ~~Retrieval (hybrid search + metadata filters + rerank) and evaluation~~ (done)
3. ~~Answer generation with citations~~ (done)
4. Scale to ~500-page documents (batch parsing, outline-based structure, parent-child retrieval)
#   E n g - R a g 
 
 
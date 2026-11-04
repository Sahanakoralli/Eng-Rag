"""Command line: python -m engrag.cli ingest | status | reset"""
import shutil
from pathlib import Path

import typer
from rich.console import Console

from engrag.config import settings

app = typer.Typer(add_completion=False, help="Engineering RAG tools")
console = Console()


@app.command()
def ingest(
    path: Path = typer.Argument(None, help="Folder of documents (default: data/raw)"),
    force: bool = typer.Option(False, "--force", help="Re-index everything"),
    images: bool = typer.Option(False, "--images", help="Save figures/tables and caption figures with a vision model (costs API credits)"),
    limit: int = typer.Option(0, "--limit", help="Process at most N new files this run (0 = all). Re-run to continue."),
    ocr: bool = typer.Option(True, "--ocr/--no-ocr", help="--no-ocr is faster and fine for PDFs that already contain text"),
    rechunk: bool = typer.Option(False, "--rechunk", help="Rebuild all chunks and vectors from the saved Markdown in data/processed (minutes; no re-parsing)"),
):
    """Parse, chunk, embed and index documents. Only new/changed files are processed."""
    from engrag.ingestion.pipeline import ingest as run

    console.print(f"[bold]Ingesting from[/bold] {path or settings.raw_dir}")
    s = run(path, force=force or rechunk, images=images, limit=limit, ocr=ocr, reuse_parsed=rechunk, log=console.print)
    console.print(
        f"\n[green]{len(s['indexed'])} indexed[/green] ({s['chunks']} chunks), "
        f"{len(s['skipped'])} unchanged, {len(s['removed'])} removed, "
        f"[red]{len(s['failed'])} failed[/red]. Total chunks in DB: {s['total_points']}"
    )
    if s.get("reused"):
        console.print(f"{s['reused']} document(s) were re-chunked from their saved Markdown (not re-parsed).")
    if s.get("deferred"):
        console.print(f"[yellow]{len(s['deferred'])} more files are waiting (limit reached). Run the same command again to continue.[/yellow]")
    if images:
        console.print(f"Figures: {s['images']} saved, {s['captioned']} captioned "
                      f"({s['caption_api_calls']} new vision API calls); tables saved: {s['tables']}")
    for doc, err in s["failed"]:
        console.print(f"[red]  {doc}[/red]: {err}")


@app.command("get-data")
def get_data(
    domain: str = typer.Option("construction", help="commerce_manufacturing | construction | energy | ..."),
    max_docs: int = typer.Option(200, help="How many PDFs to use (0 = all of the domain)"),
    keep_download: bool = typer.Option(False, "--keep-download", help="Keep the downloaded archive"),
    eval_only: bool = typer.Option(False, "--eval-only", help="Don't download PDFs: build the question file for the PDFs already in data/raw/<domain>"),
):
    """Download a UniDoc-Bench domain (CC BY-NC 4.0, non-commercial) + build eval/<domain>_eval.jsonl."""
    from engrag.evaluation import unidoc

    work = Path("data/_download")
    if eval_only:
        pdfs = unidoc.existing_pdfs(settings.raw_dir, domain) or unidoc.indexed_pdfs(settings.manifest_path, domain)
        if not pdfs:
            console.print(f"[red]No PDFs found in {settings.raw_dir}/{domain} and none in the index.[/red]")
            raise typer.Exit(1)
        qa = unidoc.load_questions(domain, work, log=console.print)
        r = unidoc.select_and_export(pdfs, qa, domain, settings.raw_dir, f"eval/{domain}_eval.jsonl", copy_files=False)
        console.print(f"\n[green]{r['questions_kept']} questions[/green] whose evidence is inside your {len(pdfs)} PDFs "
                      f"written to {r['eval_file']} (of {r['questions_total']}; skipped: {r['skipped']})")
        if r["questions_kept"] == 0:
            console.print(f"  PDF names look like: {r['sample_pdf_names']}  | evidence ids look like: {r['sample_gold_ids']}")
        shutil.rmtree(work, ignore_errors=True)
        raise typer.Exit()
    console.print(f"[bold]Downloading '{domain}'[/bold] (about 0.5-0.9 GB, first time only)...")
    pdfs, qa = unidoc.download_domain(domain, work, log=console.print)
    console.print(f"Found {len(pdfs)} PDFs and {len(qa)} questions. Selecting a subset...")
    r = unidoc.select_and_export(
        pdfs, qa, domain, settings.raw_dir, f"eval/{domain}_eval.jsonl", max_docs=max_docs or None
    )
    console.print(
        f"\n[green]Copied {r['pdfs_copied']} PDFs[/green] to {settings.raw_dir}/{domain}/\n"
        f"[green]{r['questions_kept']} questions[/green] written to {r['eval_file']} "
        f"(of {r['questions_total']}; skipped: {r['skipped']})"
    )
    if r["questions_kept"] == 0:
        console.print("[red]No questions matched the PDFs. Please send me the output above.[/red]")
        console.print(f"  PDF names look like: {r['sample_pdf_names']}")
        console.print(f"  Question evidence ids look like: {r['sample_gold_ids']}")
    if not keep_download:
        shutil.rmtree(work, ignore_errors=True)
    console.print("\nNext: python -m engrag.cli ingest")
    console.print("License reminder: CC BY-NC 4.0, non-commercial use only.")


@app.command()
def inspect(samples: int = typer.Option(3, help="How many random chunks to print")):
    """Health check of the index: sizes, page numbers, suspicious documents, random samples."""
    import json
    import random

    from rich.table import Table

    from engrag.evaluation.health import health_report, iter_payloads
    from engrag.ingestion import index as idx

    client = idx.get_client(settings.qdrant_path)
    if not client.collection_exists(settings.collection):
        console.print("[red]No index found. Run ingest (or restore your backup) first.[/red]")
        raise typer.Exit(1)
    payloads = list(iter_payloads(client, settings.collection))
    mp = Path(settings.manifest_path)
    docs = {k: v for k, v in json.loads(mp.read_text()).items() if k != "_config"} if mp.exists() else None
    r = health_report(payloads, docs)
    t = Table(title="Index health")
    t.add_column("Check"); t.add_column("Value")
    for key in ("n_docs", "n_chunks", "tokens", "chunks_per_doc", "kinds", "pdf_chunks_with_page_numbers_pct",
                "tiny_chunks_pct", "oversized_chunks_pct", "no_section_path_pct"):
        if key in r:
            t.add_row(key, str(r[key]))
    console.print(t)
    for w in r["warnings"]:
        console.print(f"[yellow]! {w}[/yellow]")
    if not r["warnings"]:
        console.print("[green]No warnings.[/green]")
    for p in random.sample(payloads, min(samples, len(payloads))):
        console.rule(f"{p['doc_id']}  |  {p.get('section_path') or '(no section)'}  |  page {p.get('page_start', '?')}  |  {p.get('chunk_kind')}")
        console.print(p["text"][:500], markup=False)
    client.close()


@app.command()
def search(
    query: str = typer.Argument(..., help="Your question"),
    k: int = typer.Option(5, help="Results to show"),
    mode: str = typer.Option("hybrid", help="hybrid | dense | sparse"),
    rerank: bool = typer.Option(False, "--rerank/--no-rerank", help="Cross-encoder rerank (slow on CPU; off by default)"),
    candidates: int = typer.Option(50, help="Chunks fetched before reranking"),
    filter: list[str] = typer.Option(None, "--filter", help="key=value, e.g. --filter doc_type=pdf (repeatable)"),
    show: int = typer.Option(600, "--show", help="Characters of each result to print (a chunk can be ~2500; use 4000 to see it all)"),
):
    """Search the index and show the best chunks (no LLM needed)."""
    from engrag.ingestion import index as idx
    from engrag.ingestion.embed import Embedder
    from engrag.retrieval.search import Retriever

    filters = dict(f.split("=", 1) for f in (filter or []))
    client = idx.get_client(settings.qdrant_path)
    hits = Retriever(client, Embedder()).search(query, k=k, candidates=candidates, mode=mode, rerank=rerank, filters=filters)
    for i, h in enumerate(hits, 1):
        console.rule(f"#{i}  score {h.score:.3f}  |  {h.citation}  |  {h.section_path}")
        console.print(h.text[:show] + (f"\n[... {len(h.text) - show} more characters: use --show {len(h.text)}]" if len(h.text) > show else ""), markup=False)
    if not hits:
        console.print("No results.")
    client.close()


@app.command()
def evaluate(
    file: Path = typer.Option(None, "--file", help="Question file (default: eval/construction_eval.jsonl)"),
    compare: bool = typer.Option(False, "--compare", help="Compare dense / BM25 / hybrid / hybrid+rerank"),
    mode: str = typer.Option("hybrid", help="hybrid | dense | sparse (ignored with --compare)"),
    rerank: bool = typer.Option(False, "--rerank/--no-rerank", help="Cross-encoder rerank (slow on CPU; off by default)"),
    candidates: int = typer.Option(50, help="Chunks fetched before reranking (lower = faster rerank, e.g. 20)"),
    limit: int = typer.Option(0, help="Only the first N questions (0 = all)"),
):
    """Score retrieval: does the correct document appear in the top results?"""
    import json
    import time

    from rich.table import Table

    from engrag.evaluation.metrics import evaluate as run_eval, load_eval
    from engrag.ingestion import index as idx
    from engrag.ingestion.embed import Embedder
    from engrag.retrieval.search import Reranker, Retriever

    path = file or Path("eval/construction_eval.jsonl")
    if not path.exists():
        console.print(f"[red]{path} not found.[/red] Build it with: python -m engrag.cli get-data --eval-only")
        raise typer.Exit(1)
    rows = load_eval(path)
    if limit:
        rows = rows[:limit]
    if not rows:
        console.print("[red]The question file has no usable questions.[/red]")
        raise typer.Exit(1)
    client = idx.get_client(settings.qdrant_path)
    r = Retriever(client, Embedder(), Reranker())
    runs = ([("dense", "dense", False), ("bm25", "sparse", False), ("hybrid", "hybrid", False),
             ("hybrid+rerank", "hybrid", True)] if compare else [(f"{mode}{'+rerank' if rerank else ''}", mode, rerank)])
    results = {}
    for name, m, rr in runs:
        console.print(f"Running [bold]{name}[/bold] on {len(rows)} questions...")
        t0 = time.time()
        results[name] = run_eval(lambda q: r.search(q, k=10, candidates=candidates, mode=m, rerank=rr), rows)
        results[name]["seconds"] = round(time.time() - t0)
    cols = ["hit@1", "hit@3", "hit@5", "hit@10", "recall@10", "mrr"]
    t = Table(title=f"Retrieval quality on {len(rows)} questions (document level)")
    t.add_column("method")
    for c_ in cols:
        t.add_column(c_, justify="right")
    t.add_column("sec", justify="right")
    for name, res in results.items():
        t.add_row(name, *[f"{res['overall'][c_]:.2f}" for c_ in cols], str(res["seconds"]))
    console.print(t)
    best_name, best = max(results.items(), key=lambda kv: kv[1]["overall"]["mrr"])
    console.print(f"\nBreakdown for the best method by MRR: [bold]{best_name}[/bold]")
    for g, v in best["by_group"].items():
        console.print(f"  {g:<40} n={v['n']:<4} hit@5={v['hit@5']:.2f}  mrr={v['mrr']:.2f}")
    out = Path("eval/results"); out.mkdir(parents=True, exist_ok=True)
    fp = out / f"{time.strftime('%Y%m%d-%H%M%S')}_failures.json"
    fp.write_text(json.dumps({k: v["failures"] for k, v in results.items()}, indent=1), encoding="utf-8")
    console.print(f"\nQuestions where the right document was NOT found: {fp}")
    client.close()


def _print_answer(a, debug=False):
    if debug:
        console.rule("What the model was shown")
        for i, h in enumerate(a.hits, 1):
            console.print(f"[{i}] {h.citation} | {h.section_path} | ~{len(h.text) // 4} tokens", style="bold", markup=False)
            console.print(h.text + "\n", markup=False)
        console.rule("Model output")
    if a.standalone and a.standalone.strip().lower() != a.question.strip().lower():
        console.print(f"(searched for: {a.standalone})", style="dim", markup=False)
    console.print("\nAnswer", style="bold")
    if a.status == "partial":
        console.print("(partial answer: the documents cover only part of the question)", style="dim", markup=False)
    console.print(a.text + "\n", markup=False)
    console.print("Sources", style="bold")
    for i, h in enumerate(a.hits, 1):
        mark = "*" if i in a.cited else " "
        link = f"  (linked: {h.payload['linked_from']})" if h.payload.get("linked_from") else ""
        console.print(f" {mark}[{i}] {h.citation}  |  {h.section_path}{link}", markup=False)
    console.print("(* = cited in the answer)")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Your question"),
    k: int = typer.Option(6, help="Chunks given to the LLM"),
    mode: str = typer.Option("hybrid", help="hybrid | dense | sparse"),
    rerank: bool = typer.Option(False, "--rerank/--no-rerank"),
    expand: bool = typer.Option(True, "--expand/--no-expand", help="Also fetch tables that the top chunks refer to"),
    session: str = typer.Option(None, "--session", help="Conversation name: remembers earlier questions so you can ask follow-ups"),
    history: int = typer.Option(4, help="How many earlier turns of the session to use"),
    filter: list[str] = typer.Option(None, "--filter", help="key=value (repeatable)"),
    debug: bool = typer.Option(False, "--debug", help="Print the full text of every source the model was given"),
):
    """Answer a question from your documents, with citations (needs an LLM API key)."""
    from engrag.generation.answer import answer
    from engrag.generation.chat import Session, chat_turn
    from engrag.ingestion import index as idx
    from engrag.ingestion.embed import Embedder
    from engrag.retrieval.search import Retriever

    filters = dict(f.split("=", 1) for f in (filter or []))
    client = idx.get_client(settings.qdrant_path)
    retriever = Retriever(client, Embedder())
    kw = dict(k=k, mode=mode, rerank=rerank, filters=filters, expand=expand)
    if session:
        a = chat_turn(question, retriever, Session(session), history_turns=history, **kw)
    else:
        a = answer(question, retriever, **kw)
    _print_answer(a, debug=debug)
    client.close()


@app.command()
def chat(
    session: str = typer.Option("default", "--session", help="Conversation name (resumes if it exists)"),
    new: bool = typer.Option(False, "--new", help="Start the session from scratch"),
    k: int = typer.Option(6),
    history: int = typer.Option(4, help="How many earlier turns to use"),
    expand: bool = typer.Option(True, "--expand/--no-expand"),
):
    """Interactive conversation with follow-up questions. Commands: /new /history /sources /quit"""
    from engrag.generation.chat import Session, chat_turn
    from engrag.ingestion import index as idx
    from engrag.ingestion.embed import Embedder
    from engrag.retrieval.search import Retriever

    sess = Session(session)
    if new:
        sess.clear()
    console.print(f"Session '{sess.name}' ({len(sess.turns)} earlier turns). Ask anything; follow-ups work. "
                  "Commands: /new  /history  /sources  /quit")
    client = idx.get_client(settings.qdrant_path)
    retriever = Retriever(client, Embedder())
    while True:
        try:
            q = console.input("\n[bold]you>[/bold] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q in ("/quit", "/exit"):
            break
        if q == "/new":
            sess.clear()
            console.print("Started a new conversation.")
            continue
        if q == "/history":
            for i, t in enumerate(sess.turns, 1):
                console.print(f"{i}. Q: {t.question}\n   A: {t.answer[:200]}", markup=False)
            continue
        if q == "/sources":
            if sess.turns:
                for s in sess.turns[-1].sources:
                    console.print(f" {'*' if s['cited'] else ' '}[{s['n']}] {s['citation']}  |  {s['section']}", markup=False)
            continue
        a = chat_turn(q, retriever, sess, history_turns=history, k=k, expand=expand)
        _print_answer(a)
    client.close()


@app.command("chat-history")
def chat_history(
    name: str = typer.Argument(None, help="Session name (omit to list all sessions)"),
    clear: bool = typer.Option(False, "--clear", help="Delete this session"),
):
    """List saved conversations, show one, or delete one."""
    from engrag.generation.chat import Session, list_sessions

    if not name:
        sessions = list_sessions()
        if not sessions:
            console.print("No saved conversations yet. Use: ask \"...\" --session NAME")
        for n, count in sessions.items():
            console.print(f"{n}  ({count} turns)", markup=False)
        return
    s = Session(name)
    if clear:
        s.clear()
        console.print(f"Deleted session '{s.name}'.")
        return
    if not s.turns:
        console.print(f"Session '{s.name}' is empty or does not exist.")
    for i, t in enumerate(s.turns, 1):
        console.print(f"{i}. Q: {t.question}", markup=False)
        if t.standalone and t.standalone != t.question:
            console.print(f"   searched for: {t.standalone}", markup=False)
        console.print(f"   A: {t.answer}", markup=False)
        if t.cited_docs:
            console.print(f"   sources: {', '.join(t.cited_docs)}", markup=False)


@app.command()
def evaluate_answers(
    file: Path = typer.Option(None, "--file", help="Question file (default: eval/construction_eval.jsonl)"),
    limit: int = typer.Option(0, help="Only the first N questions (0 = all)"),
    k: int = typer.Option(6),
    rerank: bool = typer.Option(False, "--rerank/--no-rerank"),
    expand: bool = typer.Option(True, "--expand/--no-expand", help="Also fetch tables that the top chunks refer to"),
    pause: float = typer.Option(0.0, "--pause", help="Seconds to wait between questions (use 5-10 on a free API tier)"),
):
    """Generate answers for the evaluation questions and grade them against the reference answers."""
    import json
    import time

    from rich.table import Table

    from engrag.evaluation.answers import evaluate_answers as run
    from engrag.evaluation.metrics import load_eval
    from engrag.generation.answer import answer
    from engrag.ingestion import index as idx
    from engrag.ingestion.embed import Embedder
    from engrag.retrieval.search import Retriever

    path = file or Path("eval/construction_eval.jsonl")
    if not path.exists():
        console.print(f"[red]{path} not found.[/red] Build it with: python -m engrag.cli get-data --eval-only")
        raise typer.Exit(1)
    rows = load_eval(path)
    if limit:
        rows = rows[:limit]
    console.print("Loading search models (the first time this takes about 30 seconds)...")
    client = idx.get_client(settings.qdrant_path)
    retriever = Retriever(client, Embedder())
    console.print(f"Answering and grading {len(rows)} questions with {settings.llm_provider}/{settings.llm_model}. "
                  "Each question makes 2 API calls; free tiers may add waits.")
    t_all = time.time()

    def on_start(i, n, q):
        console.print(f"[{i}/{n}] {q[:90]}", markup=False)

    def on_done(i, n, r, sec):
        console.print(f"      -> {r['verdict']}  ({sec:.0f}s; {time.time() - t_all:.0f}s total)", markup=False)
        if pause and i < n:
            time.sleep(pause)

    res = run(rows, lambda q: answer(q, retriever, k=k, rerank=rerank, expand=expand),
              progress=on_done, on_start=on_start)
    t = Table(title=f"Answer quality on {len(rows)} questions (LLM-graded)")
    cols = ["n", "correct", "partial", "incorrect", "refused", "retrieved_gold_doc", "answers_citing_gold_doc"]
    t.add_column("group")
    for c_ in cols:
        t.add_column(c_, justify="right")
    for name, s in [("ALL", res["overall"]), *res["by_group"].items()]:
        t.add_row(name, *[("-" if s.get(c_) is None else str(s[c_])) for c_ in cols])
    console.print(t)
    out = Path("eval/results"); out.mkdir(parents=True, exist_ok=True)
    fp = out / f"{time.strftime('%Y%m%d-%H%M%S')}_answers.jsonl"
    fp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in res["results"]), encoding="utf-8")
    console.print(f"\nEvery answer with the grader's reason: {fp}\n"
                  "Read the 'partial' and 'incorrect' ones yourself: the grader is an LLM and can be wrong.")
    client.close()


@app.command("sample-questions")
def sample_questions(
    file: Path = typer.Option(None, "--file", help="Question file (default: eval/construction_eval.jsonl)"),
    n: int = typer.Option(5, help="How many questions to print"),
    answer_type: str = typer.Option(None, "--answer-type", help="text_only | table_required | ..."),
    seed: int = typer.Option(0, help="Fixed seed gives the same questions each time (0 = random)"),
):
    """Print questions your indexed documents are known to answer (with the expected answer), to try with `ask`."""
    import random

    from engrag.evaluation.metrics import load_eval

    path = file or Path("eval/construction_eval.jsonl")
    if not path.exists():
        console.print(f"[red]{path} not found.[/red] Build it with: python -m engrag.cli get-data --eval-only")
        raise typer.Exit(1)
    rows = [r for r in load_eval(path) if not answer_type or r.get("answer_type") == answer_type]
    if not rows:
        console.print("No matching questions.")
        raise typer.Exit(1)
    for r in random.Random(seed or None).sample(rows, min(n, len(rows))):
        console.print(f"\n[{r.get('id')}] ({r.get('answer_type', '?')}) {r['question']}", markup=False)
        console.print(f"    expected: {r.get('reference_answer', '')[:300]}", markup=False)
        console.print(f"    found in: {', '.join(r['gold_docs'])}", markup=False)


@app.command("find-text")
def find_text(
    pattern: str = typer.Argument(..., help='Text to look for inside the index (case-insensitive). Use "" to list chunks.'),
    doc: str = typer.Option(None, "--doc", help="Only documents whose id contains this, e.g. 2692012"),
    kind: str = typer.Option(None, "--kind", help="Only chunks of this kind: text | table | mixed | code"),
    max_results: int = typer.Option(8, "--max", help="How many matches to print"),
    show: int = typer.Option(500, help="Characters to show around each match"),
):
    """Debug: is this sentence / table actually inside the index, and how was it chunked?"""
    from engrag.evaluation.health import iter_payloads
    from engrag.ingestion import index as idx

    client = idx.get_client(settings.qdrant_path)
    needle, found = pattern.lower(), 0
    for p in iter_payloads(client, settings.collection):
        if doc and doc not in p["doc_id"]:
            continue
        if kind and p.get("chunk_kind") != kind:
            continue
        pos = p["text"].lower().find(needle)
        if pos < 0:
            continue
        found += 1
        if found <= max_results:
            pages = f"p.{p['page_start']}" + (f"-{p['page_end']}" if p.get("page_end") not in (None, p["page_start"]) else "") if p.get("page_start") else "no page"
            console.rule(f"{p['doc_id']}  |  {pages}  |  kind={p.get('chunk_kind')}  |  chunk #{p.get('chunk_index')}  |  {p.get('section_path')}")
            start = max(0, pos - 80)
            console.print(p["text"][start:start + show], markup=False)
    console.print(f"\n{found} matching chunk(s)" + (f", showing the first {max_results}" if found > max_results else "") + ".")
    if found == 0:
        console.print("[yellow]Not in the index. If it is in the PDF, the parser lost it (e.g. a table stored as an image).[/yellow]")
    client.close()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Use 0.0.0.0 only if you want other computers to reach it (and set API_KEY)"),
    port: int = typer.Option(8000),
):
    """Start the web app and API: upload files, chat with them, store every question and answer."""
    from engrag.api import main

    console.print(f"Open http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}  (interactive API docs at /docs)\n"
                  "While the server runs it holds the index, so other engrag commands cannot open it.")
    main(host, port)


@app.command("list-models")
def list_models(contains: str = typer.Option(None, "--contains", help="Only models whose id contains this (NVIDIA's catalog is huge)")):
    """List the models your API key can use (Gemini or NVIDIA) for LLM_MODEL in .env."""
    from engrag.generation import llm

    provider = settings.llm_provider
    try:
        if provider == "gemini":
            names = llm.list_gemini_models()
        elif provider == "nvidia":
            names = llm.list_nvidia_models(contains or "nemotron")
        else:
            console.print(f"list-models works for gemini and nvidia; your LLM_PROVIDER is '{provider}'.")
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Could not list models: {type(e).__name__}: {str(e)[:200]}[/red]\n"
                      "Check the API key in .env (or Colab Secrets).")
        raise typer.Exit(1)
    for n in names:
        console.print(("* " if n == settings.llm_model else "  ") + n, markup=False)
    console.print(f"\nCurrent LLM_MODEL: {settings.llm_model}  (* = selected)." +
                  (" Showing ids that contain 'nemotron'; use --contains to search others." if provider == "nvidia" and not contains else ""))


@app.command()
def status():
    """Show what is indexed."""
    import json

    mp = Path(settings.manifest_path)
    if not mp.exists():
        console.print("Nothing indexed yet. Put files in data/raw and run: ingest")
        raise typer.Exit()
    m = json.loads(mp.read_text())
    docs = {k: v for k, v in m.items() if k != "_config"}
    for k, v in sorted(docs.items()):
        console.print(f"{v['chunks']:>5} chunks  {k}")
    console.print(f"\n{len(docs)} documents, {sum(v['chunks'] for v in docs.values())} chunks")


@app.command()
def reset(yes: bool = typer.Option(False, "--yes", help="Skip confirmation")):
    """Delete the vector DB and manifest (source documents are kept)."""
    if not yes and not typer.confirm("Delete the index?"):
        raise typer.Exit()
    shutil.rmtree(settings.qdrant_path, ignore_errors=True)
    Path(settings.manifest_path).unlink(missing_ok=True)
    console.print("Index deleted.")


if __name__ == "__main__":
    app()

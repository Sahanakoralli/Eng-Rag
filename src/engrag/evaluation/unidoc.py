"""Download a domain of UniDoc-Bench (Salesforce, CC BY-NC 4.0) and build an eval set.

Layout of the HF dataset (from its README):
  <domain>_pdfs.tar.gz            PDFs for that domain
  .../QA/filtered/<domain>.json   questions with gold evidence
We keep only questions answerable from text/tables (our pipeline is text-based), pick a subset of
documents that contains the evidence for those questions plus random distractors, copy the PDFs to
data/raw/<domain>/, and write eval/<domain>_eval.jsonl.
"""
import json
import random
import re
import shutil
import tarfile
from pathlib import Path

REPO = "Salesforce/UniDoc-Bench"
# The authors' original question files (with evidence links, "chunk_used") live on this branch of the
# dataset repo as <domain>.json. The main branch only has parquet tables without evidence links.
QA_REVISION = "refs/pr/5"
DOMAINS = ["commerce_manufacturing", "construction", "energy", "crm", "education", "finance", "healthcare", "legal"]
TEXT_ANSWER_TYPES = {"text_only", "table_required"}
_ID = re.compile(r"\d{5,}")


# ---------- pure logic (unit-tested) ----------

def _normalize(item: dict) -> dict:
    """Parquet/other loaders may give nested fields as JSON strings; make them real dicts."""
    item = dict(item)
    cu = item.get("chunk_used")
    if isinstance(cu, str):
        try:
            item["chunk_used"] = json.loads(cu)
        except json.JSONDecodeError:
            item["chunk_used"] = {}
    return item


def load_qa(path: Path) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        import pandas as pd  # needs pyarrow (installed with `datasets`)

        return [_normalize(r) for r in pd.read_parquet(path).to_dict("records")]
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:  # maybe JSON lines
        return [_normalize(json.loads(line)) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict):
        if "chunk_used" in data or "complete_answer" in data:  # a single QA item
            return [_normalize(data)]
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if lists else []
    return [_normalize(d) for d in data]


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def gold_ids(item: dict) -> set[str]:
    """Document ids (e.g. '0355944') behind the evidence the question actually used."""
    ids = set()
    for val in (item.get("chunk_used") or {}).values():
        if isinstance(val, dict) and val.get("used"):
            for s in _strings(val.get("metadata")):
                m = _ID.search(s)
                if m:
                    ids.add(m.group())
    return ids


def _match(gid: str, stems: dict[str, Path]) -> str | None:
    if gid in stems:
        return gid
    for s in stems:
        if gid in s:
            return s
    return None


def select_and_export(pdfs, qa, domain, raw_out, eval_out, max_docs=None, seed=42, copy_files=True) -> dict:
    stems = {p.stem: p for p in pdfs}
    usable = []  # (index, item, set of matched stems)
    skipped = {"image_or_multimodal": 0, "no_evidence_id": 0, "pdf_not_found": 0}
    for i, item in enumerate(qa):
        if item.get("answer_type") not in TEXT_ANSWER_TYPES:
            skipped["image_or_multimodal"] += 1
            continue
        gids = gold_ids(item)
        if not gids:
            skipped["no_evidence_id"] += 1
            continue
        matched = {_match(g, stems) for g in gids}
        if None in matched:
            skipped["pdf_not_found"] += 1
            continue
        usable.append((i, item, matched))

    rng = random.Random(seed)
    if not max_docs or max_docs >= len(stems):
        selected = set(stems)
    else:
        order = usable[:]
        rng.shuffle(order)
        selected: set[str] = set()
        for _, _, golds in order:  # fill ~60% of the budget with evidence docs
            if len(selected | golds) <= max_docs * 0.6:
                selected |= golds
        others = sorted(set(stems) - selected)
        rng.shuffle(others)  # the rest are distractors, like a real corpus
        selected |= set(others[: max_docs - len(selected)])

    questions = [(i, it, g) for i, it, g in usable if g <= selected]

    if copy_files:
        dest = Path(raw_out) / domain
        dest.mkdir(parents=True, exist_ok=True)
        for s in sorted(selected):
            target = dest / stems[s].name
            if not target.exists():
                shutil.copy2(stems[s], target)

    Path(eval_out).parent.mkdir(parents=True, exist_ok=True)
    with open(eval_out, "w", encoding="utf-8") as f:
        for i, it, golds in questions:
            f.write(json.dumps({
                "id": f"{domain}-{i:04d}",
                "question": it.get("rewritten_question_obscured") or it.get("question", ""),
                "reference_answer": it.get("complete_answer") or it.get("answer", ""),
                "gold_docs": sorted(f"{domain}/{stems[g].name}" for g in golds),
                "question_type": it.get("question_type"),
                "answer_type": it.get("answer_type"),
                "domain": domain,
            }, ensure_ascii=False) + "\n")

    return {"pdfs_total": len(stems), "pdfs_copied": len(selected), "questions_total": len(qa),
            "questions_kept": len(questions), "skipped": skipped, "eval_file": str(eval_out),
            # shown only when nothing matched, to help diagnose naming differences
            "sample_pdf_names": sorted(stems)[:5],
            "sample_gold_ids": sorted({g for it in qa[:50] for g in gold_ids(it)})[:5]}


# ---------- network part (needs internet + huggingface_hub) ----------

def _find_qa_file(files: list[str], domain: str) -> str | None:
    """Pick the question file for a domain from the repo listing (json/jsonl/parquet)."""
    exts = (".json", ".jsonl", ".parquet")
    cands = [f for f in files if Path(f).stem.split("-")[0] == domain and f.lower().endswith(exts)]
    cands.sort(key=lambda f: ("qa" not in f.lower(), not f.lower().endswith(".json"), len(f)))
    return cands[0] if cands else None


def _qa_via_datasets(domain: str) -> list[dict]:
    from datasets import load_dataset  # documented in the dataset README: dataset["construction"]

    return [_normalize(dict(r)) for r in load_dataset(REPO, split=domain)]


def _has_evidence(items: list[dict]) -> bool:
    return any(it.get("chunk_used") for it in items[:50])


def load_questions(domain: str, work_dir: Path, log=print) -> list[dict]:
    """Questions for a domain (small download). Prefers records that carry evidence links."""
    from huggingface_hub import hf_hub_download, list_repo_files

    if domain not in DOMAINS:
        raise ValueError(f"Unknown domain '{domain}'. Choose from: {', '.join(DOMAINS)}")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        files = list_repo_files(REPO, repo_type="dataset")
    except Exception as e:  # noqa: BLE001
        log(f"Could not list repo files ({type(e).__name__}); continuing with known file names.")
        files = []
    log(f"Repository lists {len(files)} files.")

    # --- questions: try sources in order; prefer one whose records carry evidence links ("chunk_used")
    qa_file = _find_qa_file(files, domain)
    sources = []
    if qa_file and qa_file.lower().endswith((".json", ".jsonl")):
        sources.append((qa_file, lambda: load_qa(Path(
            hf_hub_download(REPO, qa_file, repo_type="dataset", local_dir=str(work_dir))))))
    sources.append((f"branch {QA_REVISION}", lambda: load_qa(Path(hf_hub_download(
        REPO, f"{domain}.json", repo_type="dataset", revision=QA_REVISION, local_dir=str(work_dir / "qa_pr"))))))
    if qa_file and qa_file.lower().endswith(".parquet"):
        sources.append((qa_file, lambda: load_qa(Path(
            hf_hub_download(REPO, qa_file, repo_type="dataset", local_dir=str(work_dir))))))
    sources.append(("`datasets` library", lambda: _qa_via_datasets(domain)))

    qa, fallback, errors = None, None, []
    for label, load in sources:
        try:
            items = load()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {type(e).__name__}: {str(e)[:150]}")
            continue
        if not items:
            continue
        if _has_evidence(items):
            qa = items
            log(f"Questions loaded from {label} ({len(items)} questions, with evidence links).")
            break
        fallback = fallback or (label, items)
    if qa is None and fallback:
        label, qa = fallback
        log(f"WARNING: questions loaded from {label}, but they have no evidence links, so they cannot be "
            "tied to specific PDFs; the evaluation file will probably be empty.")
    if not qa:
        related = [f for f in files if domain in f.lower()][:25]
        raise RuntimeError(
            f"Could not load the questions for '{domain}'.\nTried: {errors or 'nothing matched'}\n"
            f"Repo files mentioning '{domain}': {related}\n"
            "Fix: run `pip install datasets` and try again; if the repo asks you to accept terms, open "
            "https://huggingface.co/datasets/Salesforce/UniDoc-Bench, accept them, and set HF_TOKEN."
        )
    return qa


def existing_pdfs(raw_dir, domain: str) -> list[Path]:
    d = Path(raw_dir) / domain
    return sorted(p for p in d.rglob("*.pdf") if not p.name.startswith(".") and "__MACOSX" not in p.parts) if d.exists() else []


def indexed_pdfs(manifest_path, domain: str) -> list[Path]:
    """Names of already-indexed PDFs (from the progress file); the files themselves need not exist."""
    import json

    mp = Path(manifest_path)
    if not mp.exists():
        return []
    ids = [k for k in json.loads(mp.read_text()) if k != "_config" and k.startswith(f"{domain}/") and k.lower().endswith(".pdf")]
    return [Path(i) for i in sorted(ids)]


def download_domain(domain: str, work_dir: Path, log=print):
    """Return (list_of_pdf_paths, qa_list). Questions first (small), then the 0.5-0.9 GB PDF archive."""
    from huggingface_hub import hf_hub_download

    qa = load_questions(domain, work_dir, log=log)
    work_dir = Path(work_dir)

    # --- PDFs: the archive name is given in the dataset README
    archive = hf_hub_download(REPO, f"{domain}_pdfs.tar.gz", repo_type="dataset", local_dir=str(work_dir))
    extract_to = work_dir / f"{domain}_pdfs"
    if not extract_to.exists():
        with tarfile.open(archive, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(extract_to, filter="data")
            else:
                tar.extractall(extract_to)
    pdfs = sorted(
        p for p in extract_to.rglob("*.pdf")
        if not p.name.startswith(".") and "__MACOSX" not in p.parts  # macOS metadata junk
    )
    return pdfs, qa

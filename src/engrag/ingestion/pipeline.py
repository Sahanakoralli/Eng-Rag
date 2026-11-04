"""Orchestrates parse -> chunk -> embed -> index. Incremental and failure-tolerant."""
import hashlib
import json
import re
from pathlib import Path

from engrag.config import settings
from engrag.ingestion import index as idx
from engrag.ingestion.chunk import CHUNKER_VERSION, chunk_markdown, est_tokens
from engrag.ingestion.embed import Embedder
from engrag.ingestion.parse import PAGE_BREAK, SUPPORTED_EXTS, Parsed, clean_markdown, is_junk, parse_file


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _config_signature(embedder: Embedder, images: bool, ocr: bool = True) -> dict:
    return {
        "images": images,
        "ocr": ocr,
        "embedding_model": embedder.dense_name,
        "sparse_model": embedder.sparse_name,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "chunker_version": CHUNKER_VERSION,
    }


PARSE_KEYS = {"images", "ocr"}  # the only settings that change what the parser extracts


def _changed_keys(old: dict | None, new: dict) -> set:
    if old is None:
        return set()
    changed = set()
    for k, v in new.items():
        if k in PARSE_KEYS and k not in old:
            continue  # older progress files did not record these
        if old.get(k) != v:
            changed.add(k)
    return changed


def _load_sidecar(path: Path) -> dict:
    """Optional metadata file next to a document: 'manual.pdf.meta.json'."""
    side = path.with_name(path.name + ".meta.json")
    if side.exists():
        return json.loads(side.read_text(encoding="utf-8"))
    return {}


def _title(md: str, path: Path) -> str:
    """The first heading of any level (Docling writes a PDF's title as '##'); else the file name."""
    for line in md.splitlines():
        m = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1)
    return path.stem


def ingest(raw_dir=None, embedder=None, client=None, force=False, images=False, caption_fn=None,
           limit=0, ocr=True, log=print, collection=None, manifest_path=None, processed_dir=None, only=None,
           reuse_parsed=False) -> dict:
    """Parse -> chunk -> embed -> index.

    images=True: also save figures/tables under data/processed/<doc>_assets/ and caption figures.
    limit=N:     process at most N new/changed files this run (0 = no limit). Re-run to continue.
    ocr=False:   skip OCR (faster; fine for PDFs that already contain real text).
    Progress is saved after every document, so you can stop (Ctrl+C) and resume safely.
    collection / manifest_path / processed_dir: keep a separate index (e.g. user uploads) from the main one.
    only: a set of document ids; all other files are left untouched (not even deleted from the index).
    reuse_parsed: rebuild chunks from the Markdown saved in processed_dir instead of re-parsing the files
                  (minutes instead of hours). Used automatically when only chunk/embedding settings changed.
    """
    raw = Path(raw_dir or settings.raw_dir)
    processed = Path(processed_dir or settings.processed_dir)
    manifest_path = Path(manifest_path or settings.manifest_path)
    processed.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    embedder = embedder or Embedder()
    own_client = client is None
    client = client or idx.get_client(settings.qdrant_path)
    name = collection or settings.collection

    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    sig = _config_signature(embedder, images, ocr)
    old_sig = manifest.get("_config")
    changed = _changed_keys(old_sig, sig)
    if old_sig is None or changed:
        if old_sig is not None:
            reparse = bool(changed & PARSE_KEYS)
            log(f"Settings changed ({', '.join(sorted(changed))}): re-indexing everything"
                + ("." if reparse else " from the saved Markdown (no re-parsing needed)."))
        force = True
    reuse_parsed = reuse_parsed or (old_sig is not None and bool(changed) and not (changed & PARSE_KEYS))
    docs = {k: v for k, v in manifest.items() if k != "_config"}
    old_hashes = {k: v.get("hash") for k, v in docs.items()}  # to know which saved Markdown is still valid
    stale = set()
    if docs and changed:
        stale = set(docs)  # indexed under old settings: forget their hashes so an interrupted run resumes correctly
        docs = {}

    def save_manifest():
        manifest_path.write_text(json.dumps({"_config": sig, **docs}, indent=2), encoding="utf-8")

    idx.ensure_collection(client, name, embedder.dim)
    stats = {"indexed": [], "skipped": [], "removed": [], "failed": [], "chunks": 0,
             "tables": 0, "images": 0, "captioned": 0, "caption_api_calls": 0, "reused": 0}
    cache = None
    if images and caption_fn is None:
        from engrag.generation.llm import describe_image
        from engrag.ingestion.figures import CaptionCache

        cache = caption_fn = CaptionCache(processed / "captions.json", describe_image, log=log)

    files = sorted(p for p in raw.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS and not is_junk(p.relative_to(raw)))
    seen = {p.relative_to(raw).as_posix() for p in files}
    attempted = 0
    for path in files:
        doc_id = path.relative_to(raw).as_posix()
        if only is not None and doc_id not in only:
            continue
        sidecar = _load_sidecar(path)
        digest = _sha256(path) + hashlib.sha256(json.dumps(sidecar, sort_keys=True).encode()).hexdigest()[:8]
        if not force and docs.get(doc_id, {}).get("hash") == digest:
            stats["skipped"].append(doc_id)
            continue
        if limit and attempted >= limit:
            stats.setdefault("deferred", []).append(doc_id)
            continue
        attempted += 1
        try:
            safe = doc_id.replace("/", "__")
            saved_md = processed / f"{safe}.md"
            parsed = None
            if reuse_parsed and saved_md.exists() and old_hashes.get(doc_id) == digest:  # file unchanged since it was parsed
                parsed = Parsed(saved_md.read_text(encoding="utf-8"), pages=path.suffix.lower() == ".pdf")
                stats["reused"] += 1
            if parsed is None:
                parsed = parse_file(
                    path,
                    assets_dir=(processed / f"{safe}_assets") if images else None,
                    caption_fn=caption_fn if images else None,
                    max_images=settings.max_images_per_doc,
                    ocr=ocr,
                    log=log,
                )
            md = clean_markdown(parsed.markdown)
            if not md.strip():
                raise ValueError("no text extracted (scanned image? empty file?)")
            if not saved_md.exists() or saved_md.read_text(encoding="utf-8") != md:
                saved_md.write_text(md, encoding="utf-8")
            title = sidecar.get("title") or _title(md, path)
            chunks = chunk_markdown(md, title, settings.chunk_size, settings.chunk_overlap,
                                    page_break=PAGE_BREAK if parsed.pages else None)
            if not chunks:
                raise ValueError("no chunks produced")
            texts = [c.text for c in chunks]
            payloads = [
                {
                    **sidecar,
                    "text": c.text,
                    "doc_id": doc_id,
                    "source": path.name,
                    "title": title,
                    "doc_type": path.suffix.lower().lstrip("."),
                    "section_path": c.section_path,
                    "chunk_kind": c.kind,
                    "chunk_index": c.index,
                    "tokens": est_tokens(c.text),
                    **({"page_start": c.page_start, "page_end": c.page_end} if c.page_start else {}),
                }
                for c in chunks
            ]
            dense, sparse = [], []
            for s in range(0, len(texts), 64):
                dense += embedder.dense(texts[s : s + 64])
                sparse += embedder.sparse(texts[s : s + 64])
            idx.delete_doc(client, name, doc_id)  # replace old version
            n = idx.upsert_chunks(client, name, doc_id, payloads, dense, sparse)
            docs[doc_id] = {"hash": digest, "chunks": n}
            save_manifest()  # checkpoint: a stopped run resumes from here
            stats["indexed"].append(doc_id)
            stats["chunks"] += n
            stats["tables"] += parsed.n_tables
            stats["images"] += parsed.n_images
            stats["captioned"] += parsed.n_captioned
            extra = f", {parsed.n_tables} tables, {parsed.n_images} figures ({parsed.n_captioned} captioned)" if images else ""
            log(f"  indexed {doc_id}: {n} chunks{extra}")
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
            stats["failed"].append((doc_id, f"{type(e).__name__}: {e}"))
            log(f"  FAILED  {doc_id}: {type(e).__name__}: {e}")

    for doc_id in sorted(set(docs) | stale):
        if doc_id not in seen:  # file was deleted from disk
            idx.delete_doc(client, name, doc_id)
            docs.pop(doc_id, None)
            stats["removed"].append(doc_id)

    save_manifest()
    stats["total_points"] = idx.count(client, name)
    stats["caption_api_calls"] = cache.api_calls if cache else 0
    if own_client:
        client.close()
    return stats

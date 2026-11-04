"""Step 1: turn any supported file into Markdown (headings and tables preserved).

For PDFs/Office files (via Docling) it can also save the extracted tables (CSV) and figures (PNG) and,
if a caption function is given, replace each figure with a text description so figures become searchable.
"""
import io
import re
from dataclasses import dataclass
from pathlib import Path

TEXT_EXTS = {".md", ".markdown", ".txt"}
DOCLING_EXTS = {".pdf", ".docx", ".pptx", ".html", ".htm"}
SUPPORTED_EXTS = TEXT_EXTS | DOCLING_EXTS

PAGE_BREAK = "<!-- page-break -->"
IMG_MARK = "<!-- engrag-image -->"

_converters: dict = {}


def is_junk(path: Path) -> bool:
    """macOS metadata ('._file.pdf', '__MACOSX/', '.DS_Store') and other hidden files are not documents."""
    return path.name.startswith(".") or "__MACOSX" in path.parts


_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "), ("&amp;", "&"))


def clean_markdown(md: str) -> str:
    """Docling escapes characters for Markdown rendering ('&amp;', '\\_'). We search and quote the text, so
    restore the real characters: 'Programming &amp; Architecture' -> 'Programming & Architecture'."""
    for entity, char in _ENTITIES:
        md = md.replace(entity, char)
    return md.replace("\\_", "_")


@dataclass
class Parsed:
    markdown: str
    pages: bool = False  # markdown contains PAGE_BREAK markers (real page numbers available)
    n_tables: int = 0
    n_images: int = 0
    n_captioned: int = 0


def _get_converter(with_images: bool, ocr: bool = True):
    key = (with_images, ocr)
    if key not in _converters:
        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as e:
            raise RuntimeError(
                "Docling is not installed, so PDF/DOCX/PPTX/HTML can't be parsed. "
                "Run: pip install -r requirements-parsing.txt"
            ) from e
        if with_images or not ocr:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            opts = PdfPipelineOptions()
            opts.do_ocr = ocr  # OCR is slow; born-digital PDFs already contain real text
            if with_images:
                opts.generate_picture_images = True
                opts.images_scale = 2.0
            _converters[key] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
        else:
            _converters[key] = DocumentConverter()
    return _converters[key]


def _df(table, doc):
    try:
        return table.export_to_dataframe(doc)
    except TypeError:  # older docling versions take no argument
        return table.export_to_dataframe()


def render_docling(doc, assets_dir=None, caption_fn=None, max_images=25, min_side=120, log=print) -> Parsed:
    """DoclingDocument -> Parsed. Saves tables/figures to assets_dir when given."""
    n_tables = n_images = n_cap = 0
    assets = Path(assets_dir) if assets_dir else None
    if assets:
        (assets / "tables").mkdir(parents=True, exist_ok=True)
        (assets / "images").mkdir(parents=True, exist_ok=True)
        for i, t in enumerate(doc.tables, 1):
            try:
                _df(t, doc).to_csv(assets / "tables" / f"table_{i:03d}.csv", index=False)
                n_tables += 1
            except Exception as e:  # noqa: BLE001
                log(f"    table {i} not saved: {type(e).__name__}")

    md = doc.export_to_markdown(image_placeholder=IMG_MARK, page_break_placeholder=PAGE_BREAK)
    pictures = list(doc.pictures)
    captions = [""] * len(pictures)

    if assets:
        for i, pic in enumerate(pictures, 1):
            try:
                img = pic.get_image(doc)
                if img is None or min(img.size) < min_side or n_images >= max_images:
                    continue  # logos, rules, icons, or over the per-document cap
                img.convert("RGB").save(assets / "images" / f"figure_{i:03d}.png")
                n_images += 1
                if caption_fn:
                    small = img.convert("RGB")
                    small.thumbnail((1600, 1600))
                    buf = io.BytesIO()
                    small.save(buf, "PNG")
                    captions[i - 1] = (caption_fn(buf.getvalue()) or "").strip()
                    n_cap += bool(captions[i - 1])
            except Exception as e:  # noqa: BLE001
                log(f"    image {i} skipped: {type(e).__name__}: {str(e)[:100]}")

    parts = md.split(IMG_MARK)
    if len(parts) - 1 == len(pictures):
        out = [parts[0]]
        for i, (cap, rest) in enumerate(zip(captions, parts[1:]), 1):
            flat = re.sub(r"\s+", " ", cap)
            out.append((f"[Image {i}: {flat}]" if flat else "") + rest)
        md = "".join(out)
    else:  # can't align placeholders to pictures safely: drop them rather than mislabel
        md = md.replace(IMG_MARK, "")
        n_cap = 0
    return Parsed(md, pages=True, n_tables=n_tables, n_images=n_images, n_captioned=n_cap)


def parse_file(path: Path, assets_dir=None, caption_fn=None, max_images=25, ocr=True, log=print) -> Parsed:
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        return Parsed(path.read_text(encoding="utf-8", errors="replace"))
    if ext in DOCLING_EXTS:
        with_images = bool(assets_dir or caption_fn)
        doc = _get_converter(with_images, ocr).convert(str(path)).document
        parsed = render_docling(doc, assets_dir, caption_fn, max_images=max_images, log=log)
        parsed.pages = ext == ".pdf"  # page breaks are only reliable for PDFs
        return parsed
    raise ValueError(f"Unsupported file type: {ext}")

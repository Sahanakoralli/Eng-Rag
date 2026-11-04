import pytest

pytest.importorskip("docling_core")
from PIL import Image  # noqa: E402
from docling_core.types.doc import (BoundingBox, DocItemLabel, DoclingDocument, ImageRef,  # noqa: E402
                                    ProvenanceItem, Size, TableCell, TableData)

from engrag.ingestion.figures import CaptionCache  # noqa: E402
from engrag.ingestion.parse import IMG_MARK, PAGE_BREAK, render_docling  # noqa: E402


def prov(n):
    return ProvenanceItem(page_no=n, bbox=BoundingBox(l=0, t=0, r=1, b=1), charspan=(0, 1))


def make_doc():
    doc = DoclingDocument(name="t")
    for n in (1, 2):
        doc.add_page(page_no=n, size=Size(width=600, height=800))
    doc.add_heading("Safety Manual", level=1, prov=prov(1))
    doc.add_text(label=DocItemLabel.TEXT, text="Wear a hard hat on site.", prov=prov(1))
    doc.add_picture(image=ImageRef.from_pil(Image.new("RGB", (400, 300), "white"), dpi=72), prov=prov(1))
    doc.add_heading("Loads", level=2, prov=prov(2))
    cells = [TableCell(text=t, start_row_offset_idx=r, end_row_offset_idx=r + 1,
                       start_col_offset_idx=c, end_col_offset_idx=c + 1, column_header=(r == 0))
             for r, row in enumerate([["Item", "Load kN"], ["Beam", "12"]]) for c, t in enumerate(row)]
    doc.add_table(data=TableData(table_cells=cells, num_rows=2, num_cols=2), prov=prov(2))
    doc.add_picture(image=ImageRef.from_pil(Image.new("RGB", (40, 40), "red"), dpi=72), prov=prov(2))  # logo-sized
    return doc


def test_extracts_tables_images_and_captions(tmp_path):
    calls = []
    p = render_docling(make_doc(), tmp_path / "a", caption_fn=lambda b: calls.append(b) or "Plan of level 1")
    assert "[Image 1: Plan of level 1]" in p.markdown
    assert IMG_MARK not in p.markdown and "Image 2" not in p.markdown  # tiny logo skipped
    assert (p.n_tables, p.n_images, p.n_captioned) == (1, 1, 1) and len(calls) == 1
    assert (tmp_path / "a/images/figure_001.png").exists()
    assert not (tmp_path / "a/images/figure_002.png").exists()
    assert "Load kN" in (tmp_path / "a/tables/table_001.csv").read_text()
    assert p.markdown.count(PAGE_BREAK) == 1


def test_without_assets_dir_nothing_is_written_or_captioned(tmp_path):
    p = render_docling(make_doc())
    assert IMG_MARK not in p.markdown and p.n_images == 0 and p.n_tables == 0
    assert list(tmp_path.iterdir()) == []


def test_placeholder_mismatch_never_mislabels(tmp_path):
    class Stub:
        tables, pictures = [], [object(), object()]
        def export_to_markdown(self, **kw):
            return f"text\n\n{IMG_MARK}\n\nmore"
    p = render_docling(Stub(), tmp_path / "x", caption_fn=lambda b: "cap")
    assert "cap" not in p.markdown and IMG_MARK not in p.markdown


def test_caption_failure_does_not_crash_and_is_not_cached(tmp_path):
    state = {"n": 0}
    def flaky(png):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("rate limit")
        return "ok caption"
    cache = CaptionCache(tmp_path / "c.json", flaky, log=lambda *_: None)
    assert cache(b"img") == ""            # failed, swallowed
    assert cache(b"img") == "ok caption"  # retried, since failure wasn't cached
    assert cache(b"img") == "ok caption" and state["n"] == 2  # now served from cache
    assert CaptionCache(tmp_path / "c.json", flaky)(b"img") == "ok caption"  # persisted to disk

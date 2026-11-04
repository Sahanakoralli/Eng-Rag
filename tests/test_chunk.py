from engrag.ingestion.chunk import chunk_markdown

DOC = """# Pump Manual

## 1. Safety
""" + ("Always depressurise the line before service. " * 12) + """

## 2. Specs

### 2.1 Limits
""" + ("The maximum operating temperature is 85 C under normal load. " * 10) + """

| Param | Value |
|---|---|
| Torque | 25 Nm |
| Pressure | 10 bar |
"""


def test_heading_path_and_prefix():
    chunks = chunk_markdown(DOC, "Pump Manual", max_tokens=300, min_tokens=20)
    limits = [c for c in chunks if "maximum operating temperature" in c.text]
    assert limits and limits[0].section_path == "Pump Manual > 2. Specs > 2.1 Limits"
    assert limits[0].text.startswith("Pump Manual > 2. Specs > 2.1 Limits")


def test_table_never_split_when_it_fits():
    chunks = chunk_markdown(DOC, "Pump Manual", max_tokens=300, min_tokens=20)
    tbl = [c for c in chunks if "| Torque | 25 Nm |" in c.text]
    assert len(tbl) == 1 and "| Pressure | 10 bar |" in tbl[0].text


def test_large_table_splits_with_repeated_header():
    rows = "\n".join(f"| P{i} | {i} bar |" for i in range(200))
    md = f"# T\n\n## Data\n\n| Name | Val |\n|---|---|\n{rows}\n"
    chunks = chunk_markdown(md, "T", max_tokens=100, min_tokens=5)
    tables = [c for c in chunks if c.kind == "table"]
    assert len(tables) > 1
    assert all("| Name | Val |" in c.text for c in tables)
    assert all(len(c.text) <= 100 * 4 + 200 for c in tables)  # + context prefix


def test_chunks_respect_size_limit():
    md = "# A\n\n" + ("Sentence about bolts and nuts. " * 400)
    chunks = chunk_markdown(md, "A", max_tokens=200, overlap_tokens=20)
    assert len(chunks) > 3
    assert all(len(c.text) <= 200 * 4 + 50 for c in chunks)


def test_tiny_sections_merge_forward_within_chapter():
    md = "# Doc\n\n## Ch\n\n### A\nshort\n\n### B\n" + ("Real content here. " * 40)
    chunks = chunk_markdown(md, "Doc", max_tokens=400, min_tokens=60)
    merged = [c for c in chunks if "short" in c.text]
    assert len(merged) == 1 and "Real content" in merged[0].text
    assert merged[0].section_path.endswith("A")  # labelled by where content starts


def test_tiny_sections_do_not_merge_across_chapters():
    md = "# Doc\n\n## One\nshort\n\n## Two\n" + ("Real content here. " * 40)
    chunks = chunk_markdown(md, "Doc", max_tokens=400, min_tokens=60)
    one = [c for c in chunks if "short" in c.text][0]
    assert "Real content" not in one.text and one.section_path.endswith("One")


def test_hash_inside_code_block_is_not_a_heading():
    md = "# Doc\n\n## Setup\n\n```bash\n# install deps\npip install x\n```\n" + ("text " * 80)
    chunks = chunk_markdown(md, "Doc", max_tokens=400, min_tokens=5)
    assert all("install deps" not in c.section_path for c in chunks)


def test_empty_input():
    assert chunk_markdown("", "x") == []


def test_title_not_duplicated_and_heading_only_sections_dropped():
    md = "# Pump Manual\n\n## 1. Safety\n" + ("Isolate power first. " * 20)
    chunks = chunk_markdown(md, "Pump Manual", max_tokens=300, min_tokens=20)
    assert len(chunks) == 1
    assert chunks[0].text.startswith("Pump Manual > 1. Safety\n\n")
    assert "# Pump Manual" not in chunks[0].text  # no heading-only noise


def test_page_numbers_tracked_when_page_breaks_present():
    pb = "<!-- pb -->"
    md = ("# Manual\n\n## Safety\n" + "Wear a hard hat on site. " * 30 + f"\n\n{pb}\n\n"
          "## Loads\n" + "Beam load is 12 kN. " * 30 + f"\n\n{pb}\n\n"
          "## Torque\n" + "Bolts need 25 Nm. " * 30)
    chunks = chunk_markdown(md, "Manual", max_tokens=400, min_tokens=20, page_break=pb)
    by = {c.section_path.split(" > ")[-1]: c for c in chunks}
    assert (by["Safety"].page_start, by["Safety"].page_end) == (1, 1)
    assert by["Loads"].page_start == 2 and by["Torque"].page_start == 3
    assert not any(pb in c.text for c in chunks)  # markers never leak into text


def test_no_pages_without_page_break():
    chunks = chunk_markdown("# A\n\n## B\n" + "text here. " * 50, "A", max_tokens=300, min_tokens=20)
    assert all(c.page_start is None and c.page_end is None for c in chunks)


def test_chunk_spanning_pages_gets_range():
    pb = "<!-- pb -->"
    md = "# D\n\n## S\nfirst page text\n\n" + pb + "\n\nsecond page text\n"
    c = chunk_markdown(md, "D", max_tokens=300, min_tokens=5, page_break=pb)[0]
    assert (c.page_start, c.page_end) == (1, 2)


# ---------- table compaction, heading de-duplication ----------
from engrag.ingestion.chunk import _compact_table, est_tokens  # noqa: E402
from engrag.ingestion.parse import clean_markdown  # noqa: E402

PADDED = """| Definition                                          | Stereo                                              |
|-----------------------------------------------------|-----------------------------------------------------|
| Mechanical strength of the fastenings               | 15 kg /fixing point (DIN EN 12385-4)                |
| Reaction to fire                                    | B-s2, d0                                            |"""


def test_compact_table_keeps_every_cell_and_removes_padding():
    out = _compact_table(PADDED)
    assert out.splitlines()[0] == "| Definition | Stereo |"
    assert out.splitlines()[1] == "| --- | --- |"
    assert "| Mechanical strength of the fastenings | 15 kg /fixing point (DIN EN 12385-4) |" in out
    assert est_tokens(out) < est_tokens(PADDED) / 2


def test_compact_table_keeps_alignment_marks_and_escaped_pipes():
    out = _compact_table("| a | b |\n|:---|---:|\n| x \\| y | 2 |")
    assert out.splitlines()[1] == "| :--- | ---: |" and "x \\| y" in out


def test_padded_tables_make_smaller_chunks_and_big_ones_still_repeat_the_header():
    rows = "\n".join(f"| Part number {i:>40} | {i * 7:>40} bar |" for i in range(120))
    md = f"# T\n\n## Data\n\n| Name{' ' * 40} | Value{' ' * 40} |\n|{'-' * 46}|{'-' * 48}|\n{rows}\n"
    chunks = chunk_markdown(md, "T", max_tokens=150, min_tokens=5)
    tables = [c for c in chunks if c.kind == "table"]
    assert len(tables) > 1 and all("| Name | Value |" in c.text for c in tables)
    assert all("   " not in c.text for c in tables)           # no padding survives


def test_section_heading_is_not_repeated_in_the_body_of_its_own_chunk():
    md = "# Doc\n\n## Natural balance\n" + "Text about planning. " * 30
    c = chunk_markdown(md, "Doc", max_tokens=400, min_tokens=20)[0]
    assert c.text.count("Natural balance") == 1 and c.text.startswith("Doc > Natural balance")


def test_clean_markdown_restores_entities_and_underscores():
    assert clean_markdown("Programming &amp; Architecture, a &lt; b, \\_\\_\\_ blank") == "Programming & Architecture, a < b, ___ blank"

"""E2E validation for commit a55a52b2 (subsection-repetition A+B+C fix).

Simulates the e14f3600 failure shape end-to-end, inside the ldr-local
container against the hot-mounted source:
  1. The synthesis LLM's analyze_followup output (current_knowledge)
     containing the SAME body 3x under variant headings with DIFFERENT
     citation numbers — exactly the e14f3600 payload.
  2. That output flowing through report_generator's strip → dedupe
     (Fix A) into the section body AND accumulated_findings.
  3. accumulated_findings feeding _build_previous_context (Fix B) for
     the NEXT subsection's prompt.
  4. citation handler self_check emitting dup_blocks (Fix C).

Checks (PASS/FAIL each):
  T1  Fix A: dedupe drops 2 of 3 verbatim copies; first kept.
  T2  Fix A: citation renumbering across copies still detected.
  T3  Fix A: unique sections untouched byte-for-byte.
  T4  Fix A: kept body retains [[2]] markers (cite anchors survive).
  T5  Fix A: [SEC-DEDUP] trace fires with dropped count.
  T6  Fix B: next-section prompt context contains headings+excerpt,
      NOT the full prior body (copy bait removed).
  T7  Fix B: context still carries DO NOT REPEAT fence + CRITICAL.
  T8  Fix C: self_check line contains dup_blocks= for a clean body (0)
      and for a duplicated body (>0), never triggering retry on dup
      alone.
  T9  Integration: enforce_sources_ascending_and_drop_orphans runs on
      the deduped markdown and produces a non-empty Sources block
      (dedup did not orphan the citations).
  T10 Regression guard: content WITHOUT '##' headings passes through
      dedupe unchanged (no structural assumptions).

Run inside ldr-local:
  docker cp /tmp/e2e14_subsection_dedupe.py ldr-local:/tmp/
  docker exec ldr-local /install/.venv/bin/python3 /tmp/e2e14_subsection_dedupe.py
"""
import logging
import os
import re
import sys

logging.disable(logging.CRITICAL)

# Hot-mount: redirect bytecode cache so we compile FRESH source, not
# the stale .pyc baked into the read-only site-packages (gotcha #1
# from e2e11).
sys.dont_write_bytecode = True
sys.pycache_prefix = "/tmp/e2e14_pyc"
os.makedirs(sys.pycache_prefix, exist_ok=True)

# ---------------------------------------------------------------------------
# loguru capture — monkey-patch module loggers (gotcha #2 from e2e11:
# script-level logger.add() never sees module-level bindings).
# ---------------------------------------------------------------------------
CAPTURED = []

import local_deep_research.report_generator as _rg
import local_deep_research.citation_handlers.standard_citation_handler as _sch


def _patch(mod):
    orig = mod.logger.info

    def cap(message, *a, **k):
        CAPTURED.append(str(message))
        return orig(message, *a, **k)

    mod.logger.info = cap


_patch(_rg)
_patch(_sch)

from local_deep_research.report_generator import IntegratedReportGenerator
from local_deep_research.citation_handlers.standard_citation_handler import (
    _count_duplicate_subsection_blocks,
)
from local_deep_research.text_optimization.citation_formatter import (
    enforce_sources_ascending_and_drop_orphans,
)

# ---------------------------------------------------------------------------
# Payload — lifted verbatim from the e14f3600 container log (line 2317
# window): the subsection the LLM tripled. Bodies are the REAL text so
# lengths, CJK normalization, and citation-marker placement match
# production.
# ---------------------------------------------------------------------------
URL_WIKI = "https://zh.wikipedia.org/wiki/14th_Dalai_Lama"
URL_TSEM = "https://www.tsemrinpoche.com/article.html"

BODY_13TH = (
    "第十三世达赖喇嘛土登嘉措于 1933 年圆寂，当时中国正处于军阀混战时期，西藏内部政治环境复杂。"
    "这一时期的转世灵童认定过程受到多方政治势力的介入，摄政热振活佛主导了寻访工作，"
    "并派出多路寻访队伍前往青海、西康等地考察灵童候选，考察范围较以往显著扩大。"
)
BODY_UNIQUE = (
    "第十四世达赖喇嘛的寻访队伍于 1936 年抵达青海湟中县，在当地确认了拉莫顿珠为候选灵童之一。"
    "寻访队伍观察到灵童对前世遗物的辨认表现，这一认定过程随后经过西藏地方政府的核准程序，"
    "并由摄政上报国民政府请求免于金瓶掣签，最终获得特准。"
)

# The exact failure shape: same body, variant headings, renumbered cites.
CURRENT_KNOWLEDGE = (
    f"### 第十三世达赖喇嘛圆寂后的特殊历史背景\n\n{BODY_13TH} [[2]]({URL_WIKI})\n\n"
    f"### 第十四世达赖喇嘛的寻访与认定\n\n{BODY_UNIQUE} [[12]]({URL_TSEM})\n\n"
    f"### 第十三世达赖喇嘛圆寂后的特殊历史背景\n\n{BODY_13TH} [[2]]({URL_WIKI})\n\n"
    f"### 历史背景再述\n\n{BODY_13TH} [[54]]({URL_WIKI})\n\n"
)

SOURCES = [
    {"title": "第十四世达赖喇嘛 - 维基百科", "url": URL_WIKI},
    {"title": "Tsem Rinpoche article", "url": URL_TSEM},
]


def make_generator():
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    gen.max_context_sections = 3
    gen.max_context_chars = 4000
    return gen


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def main():
    gen = make_generator()

    # --- Fix A pipeline: strip → dedupe (as in _research_and_generate_sections) ---
    stripped = gen._strip_process_leakage(CURRENT_KNOWLEDGE)
    deduped = gen._dedupe_repeated_subsections(stripped)

    t1 = deduped.count(BODY_13TH) == 1
    check("T1 dedupe keeps exactly one copy of the tripled body", t1,
          f"copies={deduped.count(BODY_13TH)}")

    t2 = "历史背景再述" not in deduped
    check("T2 renumbered-citation copy ([[54]]) still dropped", t2)

    t3 = BODY_UNIQUE in deduped and "第十四世达赖喇嘛的寻访与认定" in deduped
    check("T3 unique subsection untouched", t3)

    # Citation anchors survive in the KEPT copies.
    kept_cites = sorted(set(re.findall(r"\[\[(\d+)\]\]", deduped)))
    check("T4 kept citation anchors [[2]]+[[12]] survive dedupe",
          kept_cites == ["12", "2"], f"cites={kept_cites}")

    t5 = any("[SEC-DEDUP]" in c and "dropped=2" in c for c in CAPTURED)
    check("T5 [SEC-DEDUP] trace fired with dropped=2", t5,
          next((c[:120] for c in CAPTURED if "SEC-DEDUP" in c), "no trace"))

    # --- Fix B: the deduped content feeds accumulated_findings → next prompt ---
    # Real e14f3600 bodies run to thousands of chars — only bodies
    # LONGER than the 300-char excerpt budget are meaningful copy bait,
    # so test with a realistic-length body.
    long_body = (BODY_13TH + " ") * 3
    accumulated = [
        f"[早期生平与宗教教育（1935-1946） > 转世灵童的认定过程]\n"
        f"### 第十三世达赖喇嘛圆寂后的特殊历史背景\n\n{long_body}\n\n"
    ]
    ctx = gen._build_previous_context(accumulated)

    t6 = long_body not in ctx and "第十三世达赖喇嘛圆寂后的特殊历史背景" in ctx
    check("T6 context has headings but NOT full prior body", t6,
          f"ctx_len={len(ctx)}")
    t7 = "CONTENT ALREADY WRITTEN" in ctx and "DO NOT REPEAT" in ctx
    check("T7 DO-NOT-REPEAT fence intact", t7)

    # --- Fix C: dup_blocks in self_check ---
    dups_clean = _count_duplicate_subsection_blocks(
        f"### A\n\n{BODY_13TH}\n\n### B\n\n{BODY_UNIQUE}\n\n"
    )
    dups_dirty = _count_duplicate_subsection_blocks(CURRENT_KNOWLEDGE)
    check("T8a dup_blocks=0 on clean body", dups_clean == 0, f"{dups_clean}")
    check("T8b dup_blocks=2 on e14f3600-shaped body", dups_dirty == 2,
          f"{dups_dirty}")
    # self_check must not retry on dup alone: simulate its gate logic.
    inline_hits = len(re.findall(r"\[\[\d+\]\]", BODY_13TH))
    # (dup_blocks is deliberately absent from needs_retry composition —
    # assert the source line wires it only into the log)
    src = open(
        os.path.join(
            os.path.dirname(_sch.__file__), "standard_citation_handler.py"
        ),
        encoding="utf-8",
    ).read()
    t8c = "needs_retry = inline_cite_count == 0 or not diversity_ok" in src
    check("T8c dup_blocks never enters the retry gate", t8c)

    # --- T9: dedup does not orphan citations at enforce time ---
    # Production shape: by enforce time the citation_formatter has
    # already converted [[N]](url) to single-bracket [N](url) — feed
    # that form, not the raw synthesis output.
    enforce_input = re.sub(r"\[\[(\d+)\]\]", r"[\1]", deduped)
    # Production Sources-row format: two-line "[N] title\\nURL: url"
    # (CITE_LIST_ROW_RE) — a single-line row parses as 0 rows and
    # kills every citation as url_matched_no_surviving_row.
    sources_md = "\n\n".join(
        f"[{i+1}] {s['title']}\nURL: {s['url']}" for i, s in enumerate(SOURCES)
    )
    doc = f"{enforce_input}\n\n## Sources\n\n{sources_md}\n"
    out = enforce_sources_ascending_and_drop_orphans(doc)
    # enforce emits the ESCAPED hyperlink form [\[N\]](url) — the
    # standard production emission (9b514fa0); plain [N](url) never
    # appears post-enforce.
    has_hyperlinks = (
        re.search(r"\[\\?\[?\d+\\?\]?\]\(http", out) is not None
    )
    check("T9 enforce keeps hyperlinked citations on deduped body",
          has_hyperlinks, f"out_len={len(out)}")

    # --- T10: no-headings content passes through ---
    flat = f"{BODY_13TH}\n\n{BODY_13TH}\n\n"
    check("T10 headingless content unchanged",
          gen._dedupe_repeated_subsections(flat) == flat)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'='*50}\n{len(RESULTS)-len(failed)}/{len(RESULTS)} PASS")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

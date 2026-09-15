**DOCX export: subsections merge into the section title instead of
rendering as collapsed soft-break paragraphs.**

The previous fix split the LDR TOC paragraph into separate ``\n``-
delimited lines, but Pandoc treats single-newline separators as
soft breaks inside the same paragraph, so the rendered DOCX still
showed everything on one line ("没有正确换行" complaint). The user
additionally asked for the section title and the subsection number to
end up on the same line ("合并到上一行的带标题格式的空行中").

The new ``_prepare_markdown`` line-walking step tracks the most
recent standalone ``**Title**`` line and prepends ``Title `` to each
subsequent ``\d+\.\d+ body`` line, dropping the standalone title
row entirely. So::

    ****人物背景介绍**** 1.1 身份与基本概况 — 介绍...
    1.2 出生与早期经历 — 记录...
    1.3 家庭基本情况 — 说明...

becomes::

    **人物背景介绍 1.1 身份与基本概况 — 介绍...**
    **人物背景介绍 1.2 出生与早期经历 — 记录...**
    **人物背景介绍 1.3 家庭基本情况 — 说明...**

Each rendered DOCX paragraph now contains exactly one merged bold
heading-style line — no bullet markers, no split runs, no Pandoc
soft-break collapse. Original LDR numbering preserved verbatim.

Tests: 6 new (3 unit + 1 end-to-end Pandoc-round-trip in
``TestDOCXMergeAndLineBreaks`` verifying each subsection becomes its
own ``<w:p>`` block in the rendered DOCX). 4 existing tests updated
to reflect the new merged-line contract. 353-test Python suite
green; 616 JS tests green.

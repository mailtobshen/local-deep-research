**DOCX export: blank lines between merged bold subsections.**

The previous "merge subsection into section title" fix produced::

    **人物背景介绍 1.1 身份与基本概况 — 介绍...**
    **人物背景介绍 1.2 出生与早期经历 — 记录...**
    **人物背景介绍 1.3 家庭基本情况 — 说明...**

— but the three lines were separated by **single newlines**. Pandoc
treats single-newline separators as soft breaks inside the same
paragraph, so the rendered DOCX still had everything on one line
(``FirstParagraph`` style, one paragraph, all three subsections
concatenated). The user reported ``手工测试后，你fix的代码完全没
发挥效果，问题依旧``.

The fix: emit **blank lines** (``\n\n``) between consecutive merged
subsections so Pandoc treats each as its own paragraph. The new
``_prepare_markdown`` line-walking step inserts a ``""`` in the
output list before every merged subsection AFTER the first one,
producing::

    **人物背景介绍 1.1 ...**
    
    **人物背景介绍 1.2 ...**
    
    **人物背景介绍 1.3 ...**

End-to-end render with real Pandoc confirmed: 3 separate
``<w:p>`` paragraphs, each carrying ``<w:b />`` / ``<w:bCs />`` (bold).

Tests: 1 updated (now asserts the blank line BETWEEN subsections — was
mistakenly asserting no blank line under the old broken contract).
81-test docx suite green; 353-test Python suite green; 616 JS tests
green.

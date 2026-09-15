**DOCX export: delete (not just demote) the LLM's duplicate-title
chapter heading.**

The previous round stripped the heading style from the paragraph
whose text contained the query, but kept the text itself. The user
confirmed that was not enough — they want the LLM's
``## <query>`` heading text gone from the body, not just visually
demoted.

The ``_strip_query_heading_style`` post-processor now deletes the
entire ``<w:p>...</w:p>`` block whose ``<w:pStyle>`` is a heading
style AND whose visible text contains the query. The injected
``关于X的研究报告`` title at the top of ``<w:body>`` is the only
place the query text now appears (plus the TOC bullet line, which
is correct).

Test updated: ``test_paragraph_containing_query_is_removed_entirely``
asserts ``"量子计算基础" not in doc`` rather than only the
absence of the heading style. Smoke test with real Pandoc:
the body has exactly two occurrences of the query — the injected
title and the TOC bullet — confirming the LLM's duplicate title
is gone.

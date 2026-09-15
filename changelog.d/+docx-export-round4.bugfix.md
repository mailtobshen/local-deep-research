**Two persistent DOCX-export issues from the user's fourth round of
manual testing.**

1. **TOC was visually broken because of the ``|`` separator.**
   The report generator emits TOC subsection lines as
   ``1.1 name | _purpose_``. After the previous bullet conversion
   the line ``  - name | _purpose_`` made Pandoc split the run at
   the pipe and render the ``|`` as a literal character between two
   text runs, which the user described as "换行乱" (line-break
   mess) in the TOC. The new prep step rewrites the pipe to a
   typographic em-dash (`` — ``) inside the bullet body so Pandoc
   treats the line as a single text run.

2. **Second "title" was the LLM-generated chapter heading that
   re-used the query as its text.** LLM-generated report content
   sometimes opens a chapter with ``# <query>``. After the previous
   H1→H2 demote this was still a prominent heading in Word, which
   the user read as a duplicate title alongside the
   ``关于X的研究报告`` injected at the top. The new
   ``_strip_query_heading_style`` post-processor walks every
   paragraph in ``word/document.xml``, finds the ones that carry a
   heading ``<w:pStyle>`` AND whose visible text contains the query,
   and drops just the pStyle — the heading text stays in the
   document but renders as ordinary body text. The injected title
   becomes the only heading in the rendered .docx.

   Note: ``research.title`` (the WebUI-edited report title) is
   *not* the source of the second title. It only flows into the
   DOCX's ``core.xml`` document-properties (via Pandoc
   ``--metadata=title``), not into the body. The duplicate title
   the user saw was always the LLM-emitted body heading that
   happened to contain the query.

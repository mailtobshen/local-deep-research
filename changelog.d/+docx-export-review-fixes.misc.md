**Code-review fixes for ``DOCXExporter`` (review of the last few
rounds).** Three real issues found and fixed:

1. **Dead-code duplicate.** An earlier typo'd
   ``_check_title_already_present`` (camelCase) was left in the
   file as dead code while the real ``_title_already_present``
   (snake_case) was added below it. Removed the dead copy.

2. **Substring query match over-deleted legitimate headings.**
   The previous ``_strip_query_heading_style`` used
   ``query not in visible`` (substring match), so any section
   heading that happened to contain the query topic — e.g. the
   legitimate subsection "量子计算的发展历程" with query
   "量子计算" — was wiped from the body. Changed to
   whitespace-stripped **exact** equality (``visible == query``)
   so only the pure-duplicate ``## <query>`` paragraph is removed.
   Smoke-tested with a real report that has both a duplicate
   chapter heading and a legitimate subsection mentioning the
   query: only the duplicate is removed.

3. **Pandoc subprocess had no timeout.** A slow image fetch on
   the ``--resource-path`` path could pin the export worker
   indefinitely. Added ``timeout=30`` to ``subprocess.run`` so the
   user gets a clean ``PandocTimeout`` error instead of a hang.
   Matches the PDFService timeout guidance.

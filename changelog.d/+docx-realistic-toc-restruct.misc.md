**Real fix for the user's persistent "目录章节 **** 没转义" bug.**

Earlier rounds tried to fix the TOC pipe (``|``) issue and
``****`` artefacts, but the user's exported file kept showing literal
``****`` characters. The root cause was that **my test inputs were
synthetic** — the LDR report_generator never emits the clean
``1. **Section**`` / ``   1.1 sub`` form my tests assumed. The
actual LDR-produced TOC is one paragraph per section in the form::

    ****<Section Name>**** 1.1 sub1 | desc1 1.2 sub2 | desc2

The previous fix couldn't match ``1. **Section**`` because there
is no leading digit-dot-space in the actual content. This commit:

1. Adds a TOC-restructure step at the top of ``_prepare_markdown``
   that:
   - Rewrites ``****<Name>****`` as a stand-alone line with
     ``**`` (double) emphasis (the 4-asterisk form is what the LLM
     emits — Pandoc doesn't consume it as bold).
   - Splits the concatenated ``1.1 sub1 | desc1 1.2 sub2 | desc2``
     onto separate lines so the existing bullet-conversion regex
     (now relaxed to accept 0–3 leading spaces, since the LDR-style
     TOC has no indent) can pick each up.
2. Loosens the subsection bullet regex from exactly 3 leading
   spaces to 0–3 spaces (the LDR restructure puts subsections at
   column 0).
3. Adds 4 unit tests using the *exact* TOC paragraph copied from
   the user's exported file — proves the fix works against real
   content, not synthetic input.
4. Adds 1 end-to-end Pandoc test that pipes the real LDR TOC
   paragraph through the full pipeline and asserts NO literal
   ``****`` or ``|`` survives in any text run.
5. Adds a CJK-adjacent ``_italic_`` rewrite — ``_斜体_`` next to CJK
   characters fails Pandoc's word-boundary check, leaving the
   underscores literal. Rewritten to ``**斜体**`` (asterisks have
   no boundary requirement).

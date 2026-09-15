**Code-review hardening for the TOC-bullet ``|`` → em-dash fix.**

The round-4 fix was a one-liner that replaced every ``|`` in the
TOC bullet body with `` — `` so Pandoc stopped splitting the run at
the pipe. The unit tests covered the happy path only; this commit
adds the edge cases the review flagged:

1. **Windows line endings (CRLF).** Reports generated on Windows
   carry ``\r\n``. In multiline mode the regex's ``$`` matches
   before ``\n`` so the captured body would have ended with ``\r``,
   which Pandoc would have rendered as a literal control character
   inside the bullet line. The regex now strips a trailing ``\r``
   from the captured body before doing the pipe replace. Test:
   ``test_bullet_with_crlf_line_endings_does_not_keep_trailing_cr``.

2. **Multiple ``|`` per line.** A subsection line with multiple
   separators (e.g. ``name | a | b``) is unusual but handled: every
   ``|`` becomes `` — ``. Test:
   ``test_bullet_with_multiple_pipes_replaces_all``.

3. **Body without a pipe.** The pipe-replace is a no-op for plain
   lines. Test: ``test_bullet_without_pipe_is_unchanged``.

4. **Embedded ``**bold**`` markers in the body survive.** The
   pipe-replace must not damage surrounding markdown emphasis.
   Test: ``test_bullet_preserves_embedded_bold_markers``.

5. **End-to-end Pandoc round-trip.** Two tests run the prepped
   markdown through a real ``pypandoc.convert_text(..., "docx", ...)``
   and then assert the rendered ``word/document.xml`` has no
   literal ``*`` characters in any ``<w:t>`` (no ``****`` artifact)
   and no literal ``|`` (no pipe-split bug). This is what the
   user actually saw in Word; the existing unit tests only checked
   the prep output, not the post-Pandoc DOCX. Tests:
   ``test_rendered_toc_has_no_literal_asterisks`` and
   ``test_rendered_toc_has_no_pipe_split_text_runs``.

These four unit tests + two Pandoc round-trip tests give the
one-liner the regression coverage it should have had from the
start.

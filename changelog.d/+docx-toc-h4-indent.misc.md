**DOCX export: TOC subsection paragraphs are now indented as
Heading5.**

The LDR-generated TOC entries are now emitted as a hierarchy:

    **1.人物背景介绍**            ← bold, on its own paragraph
    ##### 1.1身份 — 介绍           ← Heading5 (indented by Pandoc template)
    ##### 1.2出生 — 记录           ← Heading5
    ##### 1.3家庭 — 说明

End-to-end render with real Pandoc shows:
* Section header on its own paragraph (FirstParagraph style)
* Each subsection on its own paragraph with Heading5 style
  (which renders with indentation in Word)
* No bullet markers
* No merged-with-prefix format that duplicated the section name

Earlier rounds had the right structure but the bullet wrapper made
the output show as a single paragraph or wrapped items as list
bullets. Removed the obsolete ``4c) merge with prefix`` step that
was overriding the new output. Subsection lines are now plain H5
headings (4-space indent would have made them code blocks; H4 gets
demoted to H5 by the cascade but keeps the heading indent).

Tests: 55 pass, JS 616/616.

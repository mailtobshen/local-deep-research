**DOCX export: report section headings now use 楷体.**

The user reported that the chapter titles in the .docx export should
render in 楷体 (regular-script Chinese typeface). The new
``_patch_styles_xml_kaiti_headings`` post-processor walks every
``Heading1..Heading6`` (plus ``Title``) style in ``word/styles.xml``
and rewrites its ``<w:rFonts ...>`` to an explicit 楷体 family
stack: ``KaiTi`` → ``SimKai`` → ``STKaiti`` → ``Noto Serif CJK TC``
→ ``serif`` (with ``w:hint="eastAsia"`` so the CJK characters are
sized with the eastAsia name). Heading *sizes* are left untouched
per the earlier "章节标题字号大小不变" requirement — only the font
family changes.

The patcher runs as step 7 of ``_inject_footer_into_docx``, right
after the existing body-font patch (which sets 宋体 五号 as the
default for unstyled body text), so the body and the headings get
their two distinct Chinese typefaces.

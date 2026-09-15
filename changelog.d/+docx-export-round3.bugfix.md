**Three more fixes to the .docx export that the user reported
during the third round of manual testing.**

1. **Image was being stretched.** The markdown image syntax we
   produced had no width/height, so Pandoc embedded the image
   stretched to fill the page column. ``_figure_to_md`` now extracts
   any explicit ``width``/``height`` from the original ``<img>``
   tag and forwards them to Pandoc as link-attribute syntax
   (``{width="200px" height="150px"}``), so the image keeps its
   natural aspect ratio.

2. **Two report titles.** The body had multiple H1s
   (``# 目录`` from the report generator and ``# <topic>`` from
   LLM-generated chapters) which Pandoc rendered as top-level
   headings next to the user's ``关于X的研究报告`` title. The new
   step in ``_prepare_markdown`` demotes every body ``#`` to ``##``
   (and every ``##`` to ``###``, cascading) so the only H1 in the
   rendered document is the user-requested title that the
   post-processor injects at the top of ``<w:body>``.

3. **Chapter title colour was tinted blue.** Pandoc's default DOCX
   template ships heading styles with ``<w:color w:val="2E74B5"/>``
   (a tinted dark blue), which the user does not want. The kaiti
   patcher now also rewrites/inserts ``<w:color w:val="000000"/>``
   on every heading style's rPr so the chapter titles render in
   pure black.

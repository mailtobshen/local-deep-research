**Three more fixes to the .docx export that the user reported
during the second round of manual testing.**

1. **Title text was just the query, not the full template.** The
   previous post-processor injected the raw query as the title
   text. Now ``_build_title_paragraph_xml`` wraps the query in
   ``关于{query}的研究报告`` before XML-escaping, so the document
   shows e.g. ``关于量子计算简介的研究报告`` instead of just
   ``量子计算简介``.

2. **Images still not in the .docx.** Pandoc's HTML reader does
   not reliably extract images from inside ``<figure>`` tags.
   ``_prepare_markdown`` now converts
   ``<figure><img ...></figure>`` (with optional ``<figcaption>``)
   to standard markdown image syntax ``![alt](url)`` before Pandoc
   sees the document — routing the image through Pandoc's native
   (well-tested) image code path. The ``<figcaption>`` text is
   used as the alt-text fallback when the ``<img>`` has no
   ``alt`` attribute (which is how ``images.store.rewrite_markdown``
   emits it). Standalone ``<img>`` tags outside any figure wrapper
   are also rewritten. Combined with the existing relative→absolute
   URL rewrite, the local Flask image routes now reach Pandoc as
   fully-resolved HTTP URLs and get embedded into the .docx.

3. **TOC asterisks + broken line breaks still showing.** The
   previous reformat only adjusted the indent of the subsection
   lines, which Pandoc's HTML reader still didn't recognise as
   nested list items. ``_prepare_markdown`` now rewrites both
   levels of the TOC to bullet lists: top-level
   ``1. **Section 1**`` becomes ``- **Section 1**`` and subsection
   lines become 2-space-indent bullets
   ``  - Sub one | _purpose one_``. Pandoc's DOCX writer renders
   a clean multi-level TOC and the bold/italic markers no longer
   leak through as literal characters.

**Two more DOCX-export polish fixes for the user's recent reports.**

1. **TOC bullet wrappers were rendered as round circles in Word.**
   The previous fix wrapped every LDR subsection in ``  - ``
   (a Markdown dash bullet) — Word renders that as a round bullet
   circle, which the user described as ``为什么你全部改为圆圈符号``.
   The fix drops the bullet wrapper entirely: the LDR subsection
   text ``1.1 身份 | 介绍`` now becomes a plain numbered line
   ``1.1 身份 — 介绍``. The original LDR numbering is preserved
   verbatim — no character is dropped or rewritten — only the
   ``|`` separator changes to an em-dash and consecutive spaces
   around it collapse. The same treatment is applied to the top-level
   section header so ``1. **Section**`` stays as ``1. **Section**``
   (no leading ``-`` bullet). The pre-existing tests that asserted
   bullet syntax were updated to expect the new plain-text contract.

2. **Image captions were rendered with a Caption style frame.**
   Pandoc's DOCX writer applies ``CaptionedFigure`` and
   ``ImageCaption`` paragraph styles to any image and its alt-text
   paragraph — Word renders those as small italic centred text
   inside a light-grey border. The user asked for ``图片不要加标题
   格式，正文格式就行`` (images in body-text format, not caption
   format). The new ``_strip_image_caption_styles`` post-processor
   drops those two pStyle references and lets the paragraphs fall
   back to the document default style. Other styles (Heading, Body,
   etc.) are untouched. Three tests added: one for the basic case,
   one verifying unrelated paragraph styles survive.

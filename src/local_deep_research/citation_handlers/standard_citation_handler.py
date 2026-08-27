"""
Standard citation handler - the original implementation.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Union

from loguru import logger

from .base_citation_handler import BaseCitationHandler


# 2026-08-25 (research 0043b4af, FPV穿越机, follow-up to commit
# 188c9f3b): the LLM sometimes ignores the inline-citation
# directive in the prompt and emits a body with zero [N] markers,
# putting every citation in the trailing `## 参考文献` block. The
# downstream ``enforce_sources_ascending_and_drop_orphans`` then
# drops every row, leaving an empty bibliography.
#
# This regex detects any of the supported inline citation forms
# (plain [N], group [N, M], hyperlink [N](url), or legacy
# [[N]](url)) so the self-check below can decide whether the
# synthesis needs a corrective retry. Matching any of these in the
# body means the LLM complied with the inline-cite directive.
_INLINE_CITE_DETECT_RE = re.compile(
    r"\[(\d+(?:\s*,\s*\d+)*)\]"           # plain [N] or [N, M]
    r"|"
    # [N](url) / [[N]](url) / escaped [\[N\]](url). The inner close
    # bracket needs its OWN optional backslash: cite_link_text emits
    # '\]', and without '\\]?' the escaped form — the standard
    # production emission — never matched (9b514fa0: distinct counts
    # silently skipped every escaped marker).
    r"\[\\?\[?\d+\\?\]?\]\([^)]+\)"
)
# Minimum number of DISTINCT sources the synthesis should cite when
# that many exist (2026-08-25, research 610f5486: 43 markers all on
# sources [2]/[3] starved the citation-anchored image pipeline — every
# section read html_covered=0). Below this the self-check retries.
_MIN_DISTINCT_SOURCES = 3


def _count_distinct_cited_sources(body: str) -> int:
    """Count DISTINCT citation numbers in *body* across all marker forms.

    Plain ``[N]`` / ``[N, M]`` groups contribute each member; hyperlinked
    ``[N](url)`` / ``[[N]](url)`` contribute their number. Duplicate
    mentions of the same number count once.

    2026-08-25 (research 16bdc6e2) fix: the hyperlinked arm's whole-match
    ``findall(r'\\d+')`` ALSO harvested digits from the URL (years, path
    segments — onion URLs almost always carry digits), so a body citing
    only [1] through [[1]](http://x.onion/2019/02/...) counted as 4.
    The diversity gate then passed on a fabricated number and skipped
    its retry. Digits are now taken from the bracketed label ONLY
    (group 1 of the plain arm; the leading ``[N]``/``[[N]]`` segment of
    the hyperlink arm — never the ``(url)`` tail).
    """
    seen: set[int] = set()
    for m in _INLINE_CITE_DETECT_RE.finditer(body):
        token = m.group(0)
        # Hyperlink form: cut at the '(' so URL digits never count.
        label = token.split("(", 1)[0] if "(" in token else token
        nums = re.findall(r"\d+", label)
        seen.update(int(n) for n in nums)
    return len(seen)


class StandardCitationHandler(BaseCitationHandler):
    """Standard citation handler with detailed analysis."""

    def analyze_initial(
        self, query: str, search_results: Union[str, List[Dict]]
    ) -> Dict[str, Any]:
        documents = self._create_documents(search_results)
        formatted_sources = self._format_sources(documents)
        current_timestamp = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )

        output_prefix = self._get_output_instruction_prefix()

        prompt = f"""{output_prefix}Analyze the following information concerning the question and include citations using numbers in square brackets [1], [2], etc. When citing, use the source number provided at the start of each source.

Question: {query}

Sources:
{formatted_sources}

Current time is {current_timestamp} UTC for verifying temporal references in sources.

Provide a detailed analysis with citations. Do not create the bibliography, it will be provided automatically.  Never make up sources. Never write or create urls. Only write text relevant to the question. Example format: "According to the research [1], ..."

CITATION FORMAT (REQUIRED, 2026-08-23 policy): every citation MUST be a hyperlink carrying the source's URL, in the form [[N]](url) — e.g. "According to the research [[1]](http://example.com/page), …". A bare [N] without a URL is NOT a valid citation and will be dropped. Copy the exact URL from the source entry you are citing."""

        response = self.llm.invoke(prompt)
        if not isinstance(response, str):
            response = response.content
        return {"content": response, "documents": documents}

    def analyze_followup(
        self,
        question: str,
        search_results: Union[str, List[Dict]],
        previous_knowledge: str,
        nr_of_links: int,
    ) -> Dict[str, Any]:
        """Process follow-up analysis with citations."""
        # 2026-08-25 observability follow-up (commit-after-9c50e0d9):
        # the standard_citation_handler.analyze_followup had ZERO
        # INFO-level log lines anywhere — neither the call's entry,
        # the LLM prompt, the self-check decision (commit 9c50e0d9),
        # the retry, nor the exit. Operators could not tell whether
        # the function had run, whether the LLM had complied with
        # the inline-cite directive, or whether the retry path had
        # been triggered. The 5-min synthesis window in research
        # f8f3a63a (2026-08-25, FPV穿越机) produced 96 events from
        # images.* / web.* modules but 0 from advanced_search_system.*,
        # making the self-check + retry behaviour invisible in
        # production logs. Emit enter + exit + self-check logs so
        # every call is greppable. Compact prefix "[CITE-INLINE]" to
        # match the existing commit-9c50e0d9 diagnostic namespace
        # and let one grep cover the whole citation flow.
        logger.info(
            f"[CITE-INLINE] analyze_followup_enter "
            f"question={(question or '')[:80]!r} "
            f"nr_of_links={nr_of_links}"
        )
        documents = self._create_documents(
            search_results, nr_of_links=nr_of_links
        )
        formatted_sources = self._format_sources(documents)
        # Add fact-checking step
        fact_check_prompt = f"""Analyze these sources for factual consistency:
1. Cross-reference major claims between sources
2. Identify and flag any contradictions
3. Verify basic facts (dates, company names, ownership)
4. Note when sources disagree

Previous Knowledge:
{previous_knowledge}

New Sources:
{formatted_sources}

        Return any inconsistencies or conflicts found."""
        if self.is_fact_checking_enabled():
            fact_check_response = self.llm.invoke(fact_check_prompt).content

        else:
            fact_check_response = ""

        current_timestamp = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )

        output_prefix = self._get_output_instruction_prefix()

        prompt = f"""{output_prefix}Using the previous knowledge and new sources, answer the question. Include citations using numbers in square brackets [1], [2], etc. When citing, use the source number provided at the start of each source. Reflect information from sources critically.

Previous Knowledge:
{previous_knowledge}

Question: {question}

New Sources:
{formatted_sources}

Current time is {current_timestamp} UTC for verifying temporal references in sources.

Reflect information from sources critically based on: {fact_check_response}. Never invent sources.
Provide a detailed answer with citations. Do not create the bibliography, it will be provided automatically. Example format: "According to [1], ..."

CITATION FORMAT (REQUIRED, 2026-08-23 policy): every citation MUST be a hyperlink carrying the source's URL, in the form [[N]](url) — e.g. "According to [[1]](http://example.com/page), …". A bare [N] without a URL is NOT a valid citation and will be dropped. Copy the exact URL from the source entry you are citing.

CITATION NUMBERING (REQUIRED, 2026-08-27, post 478a92d8): use [[N]] numbers that match the EXACT source number assigned at the start of each entry above. Sources are pre-numbered 1..K — when you cite a source, copy that exact number into your inline marker. DO NOT invent your own numbers, and DO NOT skip numbers (if sources [1]..[30] are listed, your inline markers should pick from that exact set). Verified 2026-08-27 research 478a92d8: the LLM emitted [[26]] for a source whose prompt index was 27 — the enforcer cannot fix this because the [[26]] URL happened to be a real source's URL, so it passed the orphan-check. Pick the right number the first time.

INLINE CITATION REQUIREMENT (REQUIRED, 2026-08-25, post 0043b4af): EVERY factual claim in your answer MUST carry an inline citation marker like [N] (plain), [N](url), or [[N]](url) directly in the body text. NEVER put citations only in a trailing bibliography block — they would be silently dropped by the downstream Sources enforcer. Before returning your final answer, self-check: 'Does my body contain at least 3-5 inline citation markers?' If not, add them. Verified 2026-08-24 research 0043b4af: the LLM produced a 31-row bibliography with zero body markers, leaving the user with an empty references block.

CITATION DIVERSITY (REQUIRED, 2026-08-25, post 610f5486): spread your citations across at least {_MIN_DISTINCT_SOURCES} DIFFERENT sources when that many exist — do not lean every claim on one or two sources. Different sections should cite the sources that actually support THEM. Verified 2026-08-25 research 610f5486: 43 markers all citing sources [2]/[3] starved the image pipeline (every section's cited page had no usable material)."""

        response = self.llm.invoke(prompt)
        body = response.content
        # 2026-08-25 self-check (research 0043b4af fix): the LLM
        # ignored the inline-cite directive ~half the time on
        # some darkweb queries. If the body has zero inline [N]
        # markers, retry ONCE with an explicit reminder. Capped at 1
        # retry to avoid runaway cost on a stubborn LLM — the
        # downstream orphan-drop + 188c9f3b rebuilt_empty diagnostic
        # is the fallback that surfaces the failure if the retry
        # also fails.
        inline_cite_matches = _INLINE_CITE_DETECT_RE.findall(body)
        inline_cite_count = len(inline_cite_matches)
        # 2026-08-25 (research 610f5486): diversity gate — many markers
        # on 1-2 distinct sources starves the citation-anchored image
        # pipeline. Only enforced when the pool actually offers enough
        # distinct sources to cite.
        distinct_sources = _count_distinct_cited_sources(body)
        available_sources = len(documents)
        diversity_ok = (
            distinct_sources >= _MIN_DISTINCT_SOURCES
            or available_sources < _MIN_DISTINCT_SOURCES
        )
        needs_retry = inline_cite_count == 0 or not diversity_ok
        logger.info(
            f"[CITE-INLINE] self_check "
            f"hits={inline_cite_count} "
            f"distinct={distinct_sources}/{available_sources} "
            f"action={'retry' if needs_retry else 'pass'}"
        )
        if needs_retry:
            diversity_hint = ""
            if inline_cite_count > 0 and not diversity_ok:
                diversity_hint = (
                    f"Although you wrote {inline_cite_count} markers, "
                    f"they cite only {distinct_sources} distinct "
                    f"source(s). Spread citations across at least "
                    f"{_MIN_DISTINCT_SOURCES} different sources."
                )
            retry_prompt = (
                f"{prompt}\n\n"
                "SELF-CHECK FAILED: your previous answer did not "
                "contain any inline citation markers like [N] in "
                "the body text. The downstream pipeline REQUIRES "
                "inline citations — a body with zero [N] markers "
                "leaves the user with an empty references block. "
                "Please regenerate your answer with at least 3-5 "
                "inline [N] markers (or [[N]](url) hyperlinks) "
                "directly in the body text. Each fact must carry "
                "a marker. Do NOT put all citations in a trailing "
                "block. " + diversity_hint
            )
            response = self.llm.invoke(retry_prompt)
            body = response.content
            retry_cite_count = len(_INLINE_CITE_DETECT_RE.findall(body))
            logger.info(
                f"[CITE-INLINE] self_check_retry "
                f"hits={retry_cite_count} "
                f"action={'pass' if retry_cite_count > 0 else 'failed'}"
            )
            if retry_cite_count == 0:
                logger.warning(
                    "[CITE-INLINE] self_check_retry_failed "
                    f"inline_cites=0 "
                    f"retries=1 "
                    f"reason=llm_did_not_comply_with_inline_directive "
                    f"downstream=enforce_sources_will_empty_block"
                )

        # 2026-08-25 observability follow-up: exit log with final
        # inline_cite count so operators can grep per-research inline
        # adherence stats without re-reading the report markdown.
        final_inline_cite_count = len(_INLINE_CITE_DETECT_RE.findall(body))
        final_distinct = _count_distinct_cited_sources(body)
        logger.info(
            f"[CITE-INLINE] analyze_followup_exit "
            f"inline_cites={final_inline_cite_count} "
            f"distinct={final_distinct} "
            f"retries={'0' if not needs_retry else '1'}"
        )

        return {"content": body, "documents": documents}

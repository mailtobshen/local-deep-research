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
    r"\[\\?\[?\d+\]?\]\([^)]+\)"           # [N](url) or [[N]](url)
)


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

INLINE CITATION REQUIREMENT (REQUIRED, 2026-08-25, post 0043b4af): EVERY factual claim in your answer MUST carry an inline citation marker like [N] (plain), [N](url), or [[N]](url) directly in the body text. NEVER put citations only in a trailing bibliography block — they would be silently dropped by the downstream Sources enforcer. Before returning your final answer, self-check: 'Does my body contain at least 3-5 inline citation markers?' If not, add them. Verified 2026-08-24 research 0043b4af: the LLM produced a 31-row bibliography with zero body markers, leaving the user with an empty references block."""

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
        if not _INLINE_CITE_DETECT_RE.search(body):
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
                "block."
            )
            response = self.llm.invoke(retry_prompt)
            body = response.content
            if not _INLINE_CITE_DETECT_RE.search(body):
                logger.warning(
                    "[CITE-INLINE] self_check_retry_failed "
                    f"inline_cites=0 "
                    f"retries=1 "
                    f"reason=llm_did_not_comply_with_inline_directive "
                    f"downstream=enforce_sources_will_empty_block"
                )

        return {"content": body, "documents": documents}

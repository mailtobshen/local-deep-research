import importlib
from typing import Any, Dict, List, Optional
from datetime import datetime, UTC

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from loguru import logger

# Fix circular import by importing directly from source modules
from .config.llm_config import get_llm
from .config.thread_settings import get_setting_from_snapshot
from .search_system import AdvancedSearchSystem
from .utilities import search_utilities

# Default constants for context accumulation to avoid repetition
# These are used as fallbacks when settings are not available
DEFAULT_MAX_CONTEXT_SECTIONS = (
    3  # Number of previous sections to include as context
)
DEFAULT_MAX_CONTEXT_CHARS = (
    4000  # Max characters for context (safe for smaller local models)
)


def get_report_generator(search_system=None):
    """Return an instance of the report generator with default settings.

    Args:
        search_system: Optional existing AdvancedSearchSystem to use
    """
    return IntegratedReportGenerator(search_system=search_system)


class IntegratedReportGenerator:
    def __init__(
        self,
        searches_per_section: int = 2,
        search_system=None,
        llm: BaseChatModel | None = None,
        settings_snapshot: Optional[Dict] = None,
    ):
        """
        Args:
            searches_per_section: Number of searches to perform for each
                section in the report.
            search_system: Custom search system to use, otherwise just uses
                the default.
            llm: Custom LLM to use. Required if search_system is not provided.
            settings_snapshot: Optional settings snapshot for configurable values.

        """
        # If search_system is provided, use its LLM; otherwise use the provided LLM
        self._owns_llm = False
        if search_system:
            self.search_system = search_system
            self.model = llm or search_system.model
        elif llm:
            self.model = llm
            self.search_system = AdvancedSearchSystem(llm=self.model)  # type: ignore[call-arg]
        else:
            # Fallback for backwards compatibility - will only work with auth
            self._owns_llm = True
            self.model = get_llm()
            self.search_system = AdvancedSearchSystem(llm=self.model)  # type: ignore[call-arg]

        self.searches_per_section = (
            searches_per_section  # Control search depth per section
        )

        # Load context settings from snapshot or use defaults
        self.settings_snapshot = settings_snapshot or {}
        self.max_context_sections = get_setting_from_snapshot(
            "report.max_context_sections",
            default=DEFAULT_MAX_CONTEXT_SECTIONS,
            settings_snapshot=settings_snapshot,
        )
        self.max_context_chars = get_setting_from_snapshot(
            "report.max_context_chars",
            default=DEFAULT_MAX_CONTEXT_CHARS,
            settings_snapshot=settings_snapshot,
        )

    def _get_language_directive(self) -> str:
        """Return an LLM instruction forcing the report output language.

        Reads ``report.language`` (default zh-CN). Returns "" for unknown
        values. Used to inject language into the IntegratedReportGenerator's
        own prompts (which bypass the citation handler's prefix).
        """
        lang = get_setting_from_snapshot(
            "report.language", default="zh-CN", settings_snapshot=self.settings_snapshot
        )
        label = {
            "zh-CN": "Simplified Chinese (简体中文)",
            "en": "English",
        }.get(lang)
        if not label:
            return ""
        return (
            f"IMPORTANT: Write the entire report in {label}, "
            f"regardless of the language of the sources. "
        )

    def close(self) -> None:
        """Close the LLM client if this instance created it."""
        from .utilities.resource_utils import safe_close

        if self._owns_llm:
            safe_close(self.model, "report generator LLM")

    def generate_report(
        self,
        initial_findings: Dict,
        query: str,
        progress_callback=None,
    ) -> Dict:
        """Generate a complete research report with section-specific research.

        Args:
            initial_findings: Results from initial research phase.
            query: Original user query.
            progress_callback: Optional callable(message, progress_percent, metadata)
                for reporting progress (0-100%) and checking cancellation.
        """

        # Step 1: Determine structure
        if progress_callback:
            progress_callback(
                "Determining report structure",
                0,
                {"phase": "report_structure"},
            )
        structure = self._determine_report_structure(initial_findings, query)

        # Step 2: Research and generate content for each section in one step
        sections = self._research_and_generate_sections(
            initial_findings,
            structure,
            query,
            progress_callback=progress_callback,
        )

        # Step 3: Format final report
        if progress_callback:
            progress_callback(
                "Formatting final report",
                90,
                {"phase": "report_formatting"},
            )
        report = self._format_final_report(sections, structure, query)

        if progress_callback:
            progress_callback(
                "Report complete", 100, {"phase": "report_complete"}
            )

        return report

    def _determine_report_structure(
        self, findings: Dict, query: str
    ) -> List[Dict]:
        """Analyze content and determine optimal report structure."""
        combined_content = findings["current_knowledge"]
        prompt = f"""
        Analyze this research content about: {query}

        Content Summary:
        {combined_content[:1000]}... [truncated]

        Determine the most appropriate report structure by:
        1. Analyzing the type of content (technical, business, academic, etc.)
        2. Identifying main themes and logical groupings
        3. Considering the depth and breadth of the research

        Return a table of contents structure in this exact format:
        STRUCTURE
        1. [Section Name]
           - [Subsection] | [purpose]
        2. [Section Name]
           - [Subsection] | [purpose]
        ...
        END_STRUCTURE

        Make the structure specific to the content, not generic.
        Each subsection must include its purpose after the | symbol.
        DO NOT include sections about sources, citations, references, methodology,
        scope, limitations, disclaimers, neutrality, privacy, academic integrity,
        or compliance. None of those belong in the TOC of a research report —
        they are filler.
        {self._get_language_directive()}"""

        response = search_utilities.remove_think_tags(
            str(self.model.invoke(prompt).content)
        )

        # Parse the structure
        structure: List[Dict[str, Any]] = []
        current_section: Optional[Dict[str, Any]] = None

        for line in response.split("\n"):
            if line.strip() in ["STRUCTURE", "END_STRUCTURE"]:
                continue

            if line.strip().startswith(tuple("123456789")):
                # Main section
                section_name = line.split(".")[1].strip()
                current_section = {"name": section_name, "subsections": []}
                structure.append(current_section)
            elif line.strip().startswith("-") and current_section:
                # Subsection with or without purpose
                parts = line.strip("- ").split(
                    "|", 1
                )  # Only split on first pipe
                if len(parts) == 2:
                    current_section["subsections"].append(
                        {"name": parts[0].strip(), "purpose": parts[1].strip()}
                    )
                elif len(parts) == 1 and parts[0].strip():
                    # Subsection without purpose - add default
                    current_section["subsections"].append(
                        {
                            "name": parts[0].strip(),
                            "purpose": f"Provide detailed information about {parts[0].strip()}",
                        }
                    )

        # Check if the last section is source-related and remove it
        if structure:
            last_section = structure[-1]
            section_name_lower = last_section["name"].lower()
            source_keywords = [
                "source",
                "citation",
                "reference",
                "bibliography",
            ]

            # Only check the last section for source-related content
            if any(
                keyword in section_name_lower for keyword in source_keywords
            ):
                logger.info(
                    f"Removed source-related last section: {last_section['name']}"
                )
                structure = structure[:-1]

        return structure

    def _truncate_at_sentence_boundary(self, text: str, max_chars: int) -> str:
        """Truncate text at a sentence boundary to preserve readability.

        Attempts to cut at the last sentence-ending punctuation (.!?) before
        the limit. If no suitable boundary is found within 80% of the limit,
        falls back to hard truncation.

        Args:
            text: The text to truncate
            max_chars: Maximum characters allowed

        Returns:
            Truncated text with [...truncated] marker if truncation occurred
        """
        if len(text) <= max_chars:
            return text

        truncated = text[:max_chars]

        # Look for sentence boundaries (. ! ?) followed by space or newline
        # Search backwards from the end for the last complete sentence
        last_sentence_end = -1
        for i in range(len(truncated) - 1, -1, -1):
            if truncated[i] in ".!?" and (
                i + 1 >= len(truncated) or truncated[i + 1] in " \n"
            ):
                last_sentence_end = i + 1
                break

        # Only use sentence boundary if it preserves at least 80% of content
        min_acceptable = int(max_chars * 0.8)
        if last_sentence_end > min_acceptable:
            return truncated[:last_sentence_end] + "\n[...truncated]"

        # Fallback to hard truncation
        return truncated + "\n[...truncated]"

    def _build_previous_context(self, accumulated_findings: List[str]) -> str:
        """Build context block from previously generated sections.

        Creates a formatted context block containing content from the last
        N sections (defined by self.max_context_sections) with explicit instructions
        not to repeat this content. Context is truncated if it exceeds
        self.max_context_chars to stay safe for smaller local models.

        Args:
            accumulated_findings: List of previously generated section content,
                each formatted as "[Section > Subsection]\\n{content}"

        Returns:
            Formatted context block with delimiters, or empty string if no
            previous findings exist
        """
        if not accumulated_findings:
            return ""

        recent_findings = accumulated_findings[-self.max_context_sections :]
        previous_context = "\n\n---\n\n".join(recent_findings)

        # Truncate at sentence boundary if too long
        if len(previous_context) > self.max_context_chars:
            previous_context = self._truncate_at_sentence_boundary(
                previous_context, self.max_context_chars
            )

        return (
            f"\n\n=== CONTENT ALREADY WRITTEN (DO NOT REPEAT) ===\n"
            f"{previous_context}\n"
            f"=== END OF PREVIOUS CONTENT ===\n\n"
            f"CRITICAL: The above content has already been written. Do NOT repeat "
            f"these points, examples, or explanations. Focus on NEW information "
            f"not covered above.\n"
        )

    def _build_no_boilerplate_directive(self) -> str:
        """Anti-boilerplate directive injected into every section/subsection prompt.

        Detailed-mode reports tend to grow long stretches of meta-text
        ("调查背景与范围界定", "信息局限性说明", "隐私保护原则",
        "信息来源限制", "客观中立立场", "合法合规要求", "学术诚信",
        etc.) when the LLM is asked to fill a section whose research output is
        thin. These are NOT generated from any prompt in this codebase —
        they are the model's own padding — and they have no value to the
        reader. Forbid them explicitly.
        """
        return (
            "\n\n=== OUTPUT RULES — READ CAREFULLY ===\n"
            "1. Write ONLY content that directly answers the user's research "
            "question. Every sentence must carry substantive information "
            "(facts, data, named entities, dates, numbers, mechanisms, "
            "trade-offs, examples).\n"
            "2. Do NOT include any of the following 'boilerplate / disclaimer / "
            "methodology' sections or sentences — none of them belong in a "
            "research report:\n"
            "   - '调查背景与范围界定' / '研究背景' / '调研背景'\n"
            "   - '信息局限性说明' / '局限性' / '研究局限'\n"
            "   - '隐私保护原则' / '隐私声明'\n"
            "   - '信息来源限制' / '来源说明'\n"
            "   - '客观中立立场' / '中立性声明'\n"
            "   - '合法合规要求' / '合规说明'\n"
            "   - '学术诚信' / '诚信声明'\n"
            "   - '免责声明' / 'disclaimer' / 'caveats'\n"
            "   - 'methodology' / 'scope and limitations' / 'note on sources'\n"
            "   - '## 参考文献' / '## 参考资料' / '## 引用来源' / "
            "'## References' / '## Sources' — the trailing ## Sources block "
            "is assembled by the report generator from the actual cited "
            "sources; do NOT write a per-section references list yourself, "
            "in any language.\n"
            "   - Any paragraph that merely *declares* a principle, ethics "
            "stance, or process without giving concrete findings.\n"
            "3. If the research findings on a subsection are insufficient, "
            "prefer saying briefly '*No reliable information was found on this "
            "specific point.*' over filling the section with meta-commentary.\n"
            "4. The reader does not need a methodology recap or a warning "
            "about AI-generated text. Just deliver the answer.\n"
            "=== END OF OUTPUT RULES ===\n\n"
        )

    def _research_and_generate_sections(
        self,
        initial_findings: Dict,
        structure: List[Dict],
        query: str,
        progress_callback=None,
    ) -> Dict[str, str]:
        """Research and generate content for each section in one step.

        This method processes sections sequentially, accumulating generated
        content as it goes. For each new section/subsection, it passes context
        from the last few previously generated sections to help the LLM avoid
        repetition.

        The context accumulation mechanism:
        - Tracks all generated content in accumulated_findings list
        - Before generating each section, builds context from recent findings
        - Uses self.max_context_sections (configurable, default: 3) to limit context size
        - Truncates context to self.max_context_chars (configurable, default: 4000) for safety
        - Includes explicit "DO NOT REPEAT" instructions with actual content

        Args:
            initial_findings: Results from initial research phase, may contain
                questions_by_iteration to preserve search continuity
            structure: List of section definitions, each with name and subsections
            query: Original user query for context

        Returns:
            Dict mapping section names to their generated markdown content
        """
        sections = {}

        # Per-subsection `Document` lists returned by `analyze_topic()` —
        # each entry is the documents the citation handler actually fed
        # to the LLM during that subsection's synthesis. We collect them
        # so `_format_final_report` can rebuild the Sources block from
        # the exact set the LLM cited against, instead of from the
        # post-hoc reshuffled `all_links_of_system`. This is what
        # eliminates the `[N] → wrong URL` misalignment bug.
        section_documents_per_subsection: List[List[Document]] = []

        # Accumulate content from previous sections to avoid repetition
        accumulated_findings: List[str] = []

        # Count total subsections for progress tracking
        total_subsections = sum(
            max(len(section.get("subsections", [])), 1) for section in structure
        )
        completed_subsections = 0

        # Preserve questions from initial research to avoid repetition
        # This follows the same pattern as citation tracking (all_links_of_system)
        existing_questions = initial_findings.get("questions_by_iteration", {})
        if existing_questions:
            # Set questions on both search system and its strategy
            if hasattr(self.search_system, "questions_by_iteration"):
                self.search_system.questions_by_iteration = (
                    existing_questions.copy()
                )

            # More importantly, set it on the strategy which actually uses it
            if hasattr(self.search_system, "strategy") and hasattr(
                self.search_system.strategy, "questions_by_iteration"
            ):
                self.search_system.strategy.questions_by_iteration = (
                    existing_questions.copy()
                )
                logger.info(
                    f"Initialized strategy with {len(existing_questions)} iterations of previous questions"
                )

        for i, section in enumerate(structure, 1):
            logger.info(f"Processing section: {section['name']}")
            section_content = []

            section_content.append(f"# {i}. {section['name']}\n")

            # If section has no subsections, create one from the section itself
            if not section["subsections"]:
                # Parse section name for purpose
                if "|" in section["name"]:
                    parts = section["name"].split("|", 1)
                    section["subsections"] = [
                        {"name": parts[0].strip(), "purpose": parts[1].strip()}
                    ]
                else:
                    # No purpose provided - use section name as subsection
                    section["subsections"] = [
                        {
                            "name": section["name"],
                            "purpose": f"Provide comprehensive content for {section['name']}",
                        }
                    ]

            # Process each subsection by directly researching it
            for j, subsection in enumerate(section["subsections"], 1):
                # Only add subsection header if there are multiple subsections
                if len(section["subsections"]) > 1:
                    section_content.append(f"## {i}.{j} {subsection['name']}\n")
                    section_content.append(f"_{subsection['purpose']}_\n\n")

                # Get other subsections in this section for context
                other_subsections = [
                    f"- {s['name']}: {s['purpose']}"
                    for s in section["subsections"]
                    if s["name"] != subsection["name"]
                ]
                other_subsections_text = (
                    "\n".join(other_subsections)
                    if other_subsections
                    else "None"
                )

                # Get all other sections for broader context
                other_sections = [
                    f"- {s['name']}"
                    for s in structure
                    if s["name"] != section["name"]
                ]
                other_sections_text = (
                    "\n".join(other_sections) if other_sections else "None"
                )

                # Check if this is actually a section-level content (only one subsection, likely auto-created)
                is_section_level = len(section["subsections"]) == 1

                # Build context from previously generated sections to avoid repetition
                previous_context_section = self._build_previous_context(
                    accumulated_findings
                )

                # Generate appropriate search query
                if is_section_level:
                    # Section-level prompt - more comprehensive
                    subsection_query = (
                        f"Research task: Create comprehensive content for the '{subsection['name']}' section in a report about '{query}'. "
                        f"Section purpose: {subsection['purpose']} "
                        f"\n"
                        f"Other sections in the report:\n{other_sections_text}\n"
                        f"{previous_context_section}"
                        f"This is a standalone section requiring comprehensive coverage of its topic. "
                        f"Provide a thorough exploration that may include synthesis of information from previous sections where relevant. "
                        f"Include unique insights, specific examples, and concrete data. "
                        f"Use tables to organize information where applicable. "
                        f"For conclusion sections: synthesize key findings and provide forward-looking insights. "
                        f"Build upon the research findings from earlier sections to create a cohesive narrative. "
                        f"{self._get_language_directive()}"
                        f"{self._build_no_boilerplate_directive()}"
                    )
                else:
                    # Subsection-level prompt - more focused
                    subsection_query = (
                        f"Research task: Create content for subsection '{subsection['name']}' in a report about '{query}'. "
                        f"This subsection's purpose: {subsection['purpose']} "
                        f"Part of section: '{section['name']}' "
                        f"\n"
                        f"Other sections in the report:\n{other_sections_text}\n"
                        f"\n"
                        f"Other subsections in this section will cover:\n{other_subsections_text}\n"
                        f"{previous_context_section}"
                        f"Focus ONLY on information specific to your subsection's purpose. "
                        f"Include unique details, specific examples, and concrete data. "
                        f"Use tables to organize information where applicable. "
                        f"IMPORTANT: Avoid repeating information that would logically be covered in other sections - focus on what makes this subsection unique. "
                        f"Previous research exists - find specific angles for this subsection. "
                        f"{self._get_language_directive()}"
                        f"{self._build_no_boilerplate_directive()}"
                    )

                logger.info(
                    f"Researching subsection: {subsection['name']} with query: {subsection_query}"
                )

                # Report progress and check for cancellation
                if progress_callback:
                    pct = int(
                        10
                        + (completed_subsections / max(total_subsections, 1))
                        * 80
                    )
                    progress_callback(
                        f"Researching: {section['name']} > {subsection['name']}",
                        pct,
                        {
                            "phase": "report_section_research",
                            "subsection": subsection["name"],
                        },
                    )

                # Fix iteration override: modify strategy's settings_snapshot
                # which is read dynamically via get_setting()
                strategy = self.search_system.strategy
                original_iterations = strategy.settings_snapshot.get(
                    "search.iterations"
                )
                had_iterations_key = (
                    "search.iterations" in strategy.settings_snapshot
                )
                strategy.settings_snapshot["search.iterations"] = 1
                # Belt-and-suspenders: also override max_iterations for
                # strategies that cache it at __init__ time
                original_max_iter = getattr(strategy, "max_iterations", None)
                strategy.max_iterations = 1

                try:
                    # Perform search for this subsection
                    subsection_results = self.search_system.analyze_topic(
                        subsection_query
                    )
                finally:
                    # Restore original iteration settings
                    if had_iterations_key:
                        strategy.settings_snapshot["search.iterations"] = (
                            original_iterations
                        )
                    else:
                        strategy.settings_snapshot.pop(
                            "search.iterations", None
                        )
                    if original_max_iter is not None:
                        strategy.max_iterations = original_max_iter

                completed_subsections += 1

                # Capture the documents the citation handler actually
                # emitted for this subsection. They live at
                # `findings[-1]["documents"]` (see
                # advanced_search_system/strategies/source_based_strategy.py:529-536
                # and the inner synthesis loop at line 482). Walking
                # `findings` defensively guards against strategies that
                # return a different shape.
                _findings = subsection_results.get("findings") or []
                if _findings and isinstance(_findings, list):
                    _last = _findings[-1]
                    if isinstance(_last, dict):
                        section_documents_per_subsection.append(
                            list(_last.get("documents") or [])
                        )
                    else:
                        section_documents_per_subsection.append([])
                else:
                    section_documents_per_subsection.append([])

                # Add the researched content for this subsection
                if subsection_results.get("current_knowledge"):
                    generated_content = subsection_results["current_knowledge"]
                    section_content.append(generated_content)
                    # Accumulate for context in subsequent sections
                    accumulated_findings.append(
                        f"[{section['name']} > {subsection['name']}]\n{generated_content}"
                    )
                else:
                    section_content.append(
                        "*Limited information was found for this subsection.*\n"
                    )

                section_content.append("\n\n")

            # Combine all content for this section
            sections[section["name"]] = "\n".join(section_content)

        # Stash the per-subsection documents on the instance so
        # `_format_final_report` (called next by `generate_report`) can
        # build the Sources block from the same set the LLM cited
        # against. We use an instance attribute instead of threading a
        # second return value through the public API to keep the
        # contract unchanged for any external callers.
        self._section_documents_per_subsection = section_documents_per_subsection

        return sections

    def _generate_sections(
        self,
        initial_findings: Dict,
        _section_research: Dict[str, List[Dict]],
        structure: List[Dict],
        query: str,
    ) -> Dict[str, str]:
        """
        This method is kept for compatibility but no longer used.
        The functionality has been moved to _research_and_generate_sections.
        """
        return {}

    def _format_final_report(
        self,
        sections: Dict[str, str],
        structure: List[Dict],
        query: str,
    ) -> Dict:
        """Format the final report with table of contents and sections."""
        # Generate TOC
        toc = ["# Table of Contents\n"]
        for i, section in enumerate(structure, 1):
            toc.append(f"{i}. **{section['name']}**")
            if len(section["subsections"]) > 1:
                for j, subsection in enumerate(section["subsections"], 1):
                    toc.append(
                        f"   {i}.{j} {subsection['name']} | _{subsection['purpose']}_"
                    )

        # Combine TOC and sections
        report_parts = ["\n".join(toc), ""]

        # Add a summary of the research
        report_parts.append("# Research Summary")
        report_parts.append(
            "This report was researched using an advanced search system."
        )
        report_parts.append(
            "Research included targeted searches for each section and subsection."
        )
        report_parts.append("\n---\n")

        # Add each section's content
        for section in structure:
            if section["name"] in sections:
                report_parts.append(sections[section["name"]])
                report_parts.append("")

        # Build the trailing Sources block. When per-subsection documents
        # were captured by `_research_and_generate_sections`, run the
        # citation-alignment + sequential 1..N renumbering pass against
        # them; otherwise fall through to the legacy
        # `format_links_to_markdown(all_links_of_system)` path.
        per_subsection_docs: List[List[Document]] = getattr(
            self, "_section_documents_per_subsection", []
        ) or []
        if per_subsection_docs:
            from .text_optimization.citation_formatter import (
                build_first_cite_order,
                renumber_citations,
                strip_hallucinated_citations,
                strip_per_section_sources_block,
            )
            from .utilities.url_utils import canonical_url_key

            # Flatten documents in subsection iteration order so the
            # `first_cite_order` reflects the same body order.
            all_docs: List[Document] = []
            for sub_docs in per_subsection_docs:
                all_docs.extend(sub_docs)

            # 0) Strip any per-section sources/references block the LLM
            #    may have appended. Done BEFORE the renumbering pass so
            #    the citation numbers in the dropped block do not skew
            #    the global first-cite order. The trailing
            #    ``## Sources`` assembled at the end of this method is
            #    the single source of truth.
            for name in list(sections.keys()):
                sections[name] = strip_per_section_sources_block(
                    sections[name]
                )

            # 1) Global validity set from doc indices.
            valid_indices = {
                d.metadata["index"]
                for d in all_docs
                if isinstance(d.metadata.get("index"), int)
            }

            # 2) Strip hallucinated markers BEFORE building the order so
            #    a hallucinated `[N]` in section 2 doesn't get counted.
            for name in list(sections.keys()):
                sections[name] = strip_hallucinated_citations(
                    sections[name], valid_indices
                )

            # 3) First-cite order across all sections, in TOC order.
            body_order: List[int] = []
            seen: set = set()
            for section in structure:
                body = sections.get(section["name"], "")
                for n in build_first_cite_order(body, valid_indices):
                    if n not in seen:
                        seen.add(n)
                        body_order.append(n)

            # 4) old -> new (1-based) remap.
            old_to_new: Dict[int, int] = {
                old: new + 1 for new, old in enumerate(body_order)
            }

            # 5) Build the new `sources` mapping keyed by NEW index, so
            #    `renumber_citations` can supply URLs for plain `[N]`
            #    tokens whose original URL was never hyperlinked.
            new_sources: Dict[int, tuple] = {}
            for old_idx in body_order:
                doc = next(
                    (
                        d
                        for d in all_docs
                        if d.metadata.get("index") == old_idx
                    ),
                    None,
                )
                if doc is None:
                    continue
                new_sources[old_to_new[old_idx]] = (
                    doc.metadata.get("title", ""),
                    doc.metadata.get("source", ""),
                )

            # 6) Rewrite body markers per the remap.
            for name in list(sections.keys()):
                sections[name] = renumber_citations(
                    sections[name], new_sources, old_to_new
                )

            # 7) Rebuild the report content from the rewritten sections
            #    (the first two TOC/summary parts are unchanged).
            body_parts: List[str] = []
            for section in structure:
                if section["name"] in sections:
                    body_parts.append(sections[section["name"]])
                    body_parts.append("")
            report_parts = [report_parts[0], ""] + report_parts[1:1] + body_parts

            # 8) Build the Sources block from `all_docs`, deduped by
            #    canonical URL. First-seen (subsection order) wins.
            canon_to_doc: Dict[str, Document] = {}
            for d in all_docs:
                idx = d.metadata.get("index")
                if not isinstance(idx, int) or idx not in old_to_new:
                    continue
                url = d.metadata.get("source", "") or ""
                canon = canonical_url_key(url) or url or f"local-{idx}"
                if canon not in canon_to_doc:
                    canon_to_doc[canon] = d

            sources_lines: List[str] = []
            for canon, d in canon_to_doc.items():
                new_idx = old_to_new[d.metadata["index"]]
                title = d.metadata.get("title", "Untitled")
                sources_lines.append(
                    f"[{new_idx}] {title} (source nr: {new_idx})\n"
                    f"   URL: {canon}\n\n"
                )
            formatted_all_links = "".join(sources_lines)
        else:
            # Back-compat: original behaviour, no renumbering. Still
            # strip any per-section sources/references block the LLM
            # appended, so the trailing ``## Sources`` stays the single
            # source of truth for every caller of this code path
            # (scheduler, mcp_strategy, langgraph_agent).
            from .text_optimization.citation_formatter import (
                strip_per_section_sources_block,
            )

            for name in list(sections.keys()):
                sections[name] = strip_per_section_sources_block(
                    sections[name]
                )
            body_parts: List[str] = []
            for section in structure:
                if section["name"] in sections:
                    body_parts.append(sections[section["name"]])
                    body_parts.append("")
            report_parts = [report_parts[0], ""] + report_parts[1:1] + body_parts

            utilities = importlib.import_module("local_deep_research.utilities")
            formatted_all_links = (
                utilities.search_utilities.format_links_to_markdown(
                    all_links=self.search_system.all_links_of_system
                )
            )

        # Create final report with all parts
        final_report_content = "\n\n".join(report_parts)
        final_report_content = (
            final_report_content + "\n\n## Sources\n\n" + formatted_all_links
        )

        # Create metadata dictionary
        metadata = {
            "generated_at": datetime.now(UTC).isoformat(),
            "initial_sources": len(self.search_system.all_links_of_system),
            "sections_researched": len(structure),
            "searches_per_section": self.searches_per_section,
            "query": query,
        }

        # Return both content and metadata
        return {"content": final_report_content, "metadata": metadata}

    def _generate_error_report(self, query: str, error_msg: str) -> str:
        return f"=== ERROR REPORT ===\nQuery: {query}\nError: {error_msg}"

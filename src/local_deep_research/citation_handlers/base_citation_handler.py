"""
Base class for all citation handlers.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Union

from langchain_core.documents import Document
from loguru import logger


class BaseCitationHandler(ABC):
    """Abstract base class for citation handlers."""

    # Maps report.language setting values to an LLM-facing language name.
    _LANG_LABEL = {
        "zh-CN": "Simplified Chinese (简体中文)",
        "en": "English",
    }

    def __init__(self, llm, settings_snapshot=None):
        self.llm = llm
        self.settings_snapshot = settings_snapshot or {}
        self._fact_checking_logged = False

    def get_setting(self, key: str, default=None):
        """Get a setting value from the snapshot."""
        if key in self.settings_snapshot:
            value = self.settings_snapshot[key]
            # Extract value from dict structure if needed
            if isinstance(value, dict) and "value" in value:
                return value["value"]
            return value
        return default

    def is_fact_checking_enabled(self) -> bool:
        """Check if fact-checking is enabled and log the state once."""
        enabled = self.get_setting("general.enable_fact_checking", False)
        if not self._fact_checking_logged:
            handler_name = type(self).__name__
            if enabled:
                logger.info(
                    f"[{handler_name}] Fact-checking is ENABLED — "
                    f"extra LLM call per synthesis"
                )
            else:
                logger.info(f"[{handler_name}] Fact-checking is DISABLED")
            self._fact_checking_logged = True
        return bool(enabled)

    def _get_language_directive(self) -> str:
        """Return an LLM instruction forcing the report output language.

        Reads the ``report.language`` setting (default zh-CN). Returns an
        empty string for unknown values so custom/unsupported codes don't
        inject a broken directive.
        """
        lang = self.get_setting("report.language", "zh-CN")
        label = self._LANG_LABEL.get(lang)
        if not label:
            return ""
        return (
            f"IMPORTANT: Write the entire response/report in {label}, "
            f"regardless of the language of the sources. "
        )

    def _get_output_instruction_prefix(self) -> str:
        """
        Get formatted output instructions from settings if present.

        This allows users to customize output language, tone, style, and formatting
        for research answers and reports. Instructions are prepended to prompts
        sent to the LLM.

        Returns:
            str: Formatted instruction prefix if custom instructions are set,
                 empty string otherwise.

        Examples:
            - "Respond in Spanish with formal academic tone"
            - "Use simple language suitable for beginners"
            - "Be concise with bullet points"
        """
        language_directive = self._get_language_directive()

        output_instructions = self.get_setting(
            "general.output_instructions", ""
        ).strip()

        prefix = language_directive
        if output_instructions:
            prefix += f"User-Specified Output Style: {output_instructions}\n\n"
        elif language_directive:
            prefix += "\n\n"
        return prefix

    def _create_documents(
        self, search_results: Union[str, List[Dict]], nr_of_links: int = 0
    ) -> List[Document]:
        """
        Convert search results to LangChain documents format and add index
        to original search results.
        """
        documents: List[Document] = []
        if isinstance(search_results, str):
            return documents

        for i, result in enumerate(search_results):
            if isinstance(result, dict):
                # Add index to the original search result dictionary if it doesn't exist
                # This preserves indices that were already set (e.g., for topic organization)
                if "index" not in result:
                    result["index"] = str(i + nr_of_links + 1)

                content = result.get("full_content", result.get("snippet", ""))
                # Use the index from the result if it exists, otherwise calculate it
                doc_index = int(result.get("index", i + nr_of_links + 1))
                documents.append(
                    Document(
                        page_content=content,
                        metadata={
                            "source": result.get("link", f"source_{i + 1}"),
                            "title": result.get("title", f"Source {i + 1}"),
                            "index": doc_index,
                        },
                    )
                )
        return documents

    def _format_sources(self, documents: List[Document]) -> str:
        """Format sources with numbers for citation."""
        sources = []
        for doc in documents:
            source_id = doc.metadata["index"]
            sources.append(f"[{source_id}] {doc.page_content}")
        return "\n\n".join(sources)

    @abstractmethod
    def analyze_initial(
        self, query: str, search_results: Union[str, List[Dict]]
    ) -> Dict[str, Any]:
        """Process initial analysis with citations."""
        pass

    @abstractmethod
    def analyze_followup(
        self,
        question: str,
        search_results: Union[str, List[Dict]],
        previous_knowledge: str,
        nr_of_links: int,
    ) -> Dict[str, Any]:
        """Process follow-up analysis with citations."""
        pass

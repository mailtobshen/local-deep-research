"""
LangGraph agent-based research strategy with parallel subagent support.

Uses LangChain's create_agent() to build a tool-calling agent that autonomously
decides what to search, when to dig deeper, and when to synthesize. Complex
questions can be decomposed into subtopics researched in parallel by subagents.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import UTC, datetime
from typing import Any, Dict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from loguru import logger

from ...citation_handler import CitationHandler
from ...config.thread_settings import get_bool_setting_from_snapshot
from ...exceptions import ResearchTerminatedException
from ...text_optimization.citation_formatter import (
    CITE_LIST_ROW_RE,
    find_sources_section,
)
from ...utilities.search_utilities import (
    extract_links_from_search_results,
    format_links_to_markdown,
)
from ..tools.fetch import FETCH_MODES, build_fetch_tool
from .base_strategy import BaseSearchStrategy


def _parse_sources_markdown_urls(formatted_output: str) -> set[str]:
    """Return the set of URLs cited in the report's trailing Sources block.

    Parses the ``## Sources`` (or ``## 参考文献``) section produced by
    ``format_links_to_markdown``. Each row has the form
    ``[N] Title (source nr: ...)\\n   URL: <url>``. The URLs are
    returned exactly as written (canonical form, since
    ``format_links_to_markdown`` runs ``canonical_url_key`` itself).

    Empty when the document has no References block or no rows with
    a ``URL:`` line. Rows without a URL are silently skipped — the
    caller cannot act on them.
    """
    out: set[str] = set()
    start = find_sources_section(formatted_output)
    if start < 0:
        return out
    sources_content = formatted_output[start:]
    for m in CITE_LIST_ROW_RE.finditer(sources_content):
        url = (m.group(3) or "").strip()
        if url:
            out.add(url)
    return out


def _normalize_for_lookup(url: str) -> str:
    """Normalise a URL for in-memory lookup: lower-case host, drop
    fragment, strip trailing slash on path. Empty/None-safe."""
    from urllib.parse import urlparse, urlunparse

    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        path = p.path.rstrip("/") or ""
        return urlunparse(
            (p.scheme.lower(), p.netloc.lower(), path, p.params, p.query, "")
        )
    except Exception:
        return url.strip()


def _select_urls_to_fetch(
    all_search_results: list, allowed_urls: set[str] | None
) -> list[str]:
    """Compute the deduped list of URLs to fetch.

    When ``allowed_urls`` is given, the set itself is the source of
    truth (typically the cited URLs from the markdown References
    block). We only skip a URL if the agent already has
    ``html_content`` for it in ``all_search_results`` — a cited URL
    the agent never searched is still fetched.

    When ``allowed_urls`` is None, the legacy behaviour is preserved:
    fetch every URL in ``all_search_results`` that is missing
    ``html_content``.

    Returns URLs in the order they were first encountered.
    """
    fetched_lookup: dict[str, str] = {}
    for r in all_search_results or []:
        if not isinstance(r, dict):
            continue
        url = r.get("link") or r.get("url")
        if not url:
            continue
        fetched_lookup[_normalize_for_lookup(url)] = (
            r.get("html_content") or ""
        )

    if allowed_urls is None:
        candidates = [
            u for u, content in fetched_lookup.items() if not content
        ]
    else:
        candidates = [
            u
            for u in allowed_urls
            if u and not fetched_lookup.get(_normalize_for_lookup(u))
        ]

    seen: set[str] = set()
    out: list[str] = []
    for u in candidates:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_ITERATIONS = (
    50  # agent needs many more cycles than pipeline strategies
)
MIN_ITERATIONS = 10  # below this the agent can barely do anything useful
SUBAGENT_TIMEOUT_SECONDS = 1800  # 30 minutes per subagent
MAX_SUBTOPICS = 8
MAX_SUBAGENT_WORKERS = 4
# CONTENT_FETCH_TIMEOUT and CONTENT_MAX_LENGTH live alongside the fetch
# tool builders in advanced_search_system/tools/fetch/.


# ---------------------------------------------------------------------------
# Thread-safe search result collector
# ---------------------------------------------------------------------------


class SearchResultsCollector:
    """Accumulates search results from the lead agent and subagents.

    Thread-safe: multiple subagent threads may call ``add_results``
    concurrently.  The ``_all_links`` reference points to the strategy's
    shared ``all_links_of_system`` list and is never reassigned.
    """

    def __init__(self, all_links: list | None = None) -> None:
        self._results: list[dict] = []
        self._sources: list[str] = []
        self._lock = threading.Lock()
        self._all_links = all_links if all_links is not None else []

    # -- public API ----------------------------------------------------------

    def add_results(
        self,
        results: list[dict],
        engine_name: str = "web",
    ) -> int:
        """Index *results* and append to the internal list **and** the shared
        ``all_links_of_system``.  Returns the starting citation index
        (0-based) assigned to the first result in this batch.

        The entire operation runs under a single lock acquisition so that
        citation indices are never duplicated.
        """
        if not results:
            return len(self._all_links)

        with self._lock:
            # Use global offset (all_links) not per-call offset (results)
            # so that indices are unique across sections in detailed reports.
            start_idx = len(self._all_links)
            for i, raw in enumerate(results):
                if not isinstance(raw, dict):
                    continue
                r = dict(raw)  # shallow copy to avoid mutating engine output
                r["index"] = str(start_idx + i + 1)
                r["source_engine"] = engine_name
                # Normalise URL key — citation handler expects "link"
                if "link" not in r and "url" in r:
                    r["link"] = r["url"]
                self._results.append(r)
                link = r.get("link", "")
                if link:
                    self._sources.append(link)
                self._all_links.append(r)
            return start_idx

    def find_by_url(self, url: str) -> int | None:
        """Return the 1-based citation index if *url* is already tracked, else ``None``."""
        with self._lock:
            for r in self._all_links:
                if r.get("link", r.get("url", "")) == url:
                    idx = r.get("index")
                    if idx is not None:
                        return int(idx)
                    return None
            return None

    def attach_html_content(self, url: str, html_content: str) -> bool:
        """Attach serialized image data (``html_content``) to the record(s)
        already registered for *url*.

        Returns ``True`` if a matching record was found and updated. Used by
        the fetch tool when ``report.enable_images`` is on so the report-stage
        image enhancer can read images from ``search_results[].html_content``
        (same storage convention as ``full_search._get_full_content``).
        """
        with self._lock:
            updated = False
            for collection in (self._results, self._all_links):
                for r in collection:
                    if r.get("link", r.get("url", "")) == url:
                        r["html_content"] = html_content
                        updated = True
            return updated

    def reset(self) -> None:
        """Clear per-call state.  ``_all_links`` is intentionally kept."""
        with self._lock:
            self._results.clear()
            self._sources.clear()

    @property
    def results(self) -> list[dict]:
        with self._lock:
            return list(self._results)

    @property
    def sources(self) -> list[str]:
        with self._lock:
            return list(self._sources)


# ---------------------------------------------------------------------------
# Tool factory helpers
# ---------------------------------------------------------------------------


def _format_results(results: list[dict], start_idx: int) -> str:
    """Format search results as ``[N] Title (URL)\\nSnippet``."""
    lines = []
    for i, r in enumerate(results):
        if not isinstance(r, dict):
            continue
        idx = start_idx + i + 1
        title = r.get("title", "No title")
        link = r.get("link", r.get("url", ""))
        snippet = r.get("snippet", r.get("body", ""))
        lines.append(f"[{idx}] {title} ({link})\n{snippet}")
    return "\n\n".join(lines) if lines else "No results."


def _make_web_search_tool(
    search_engine_name: str,
    model: BaseChatModel,
    settings_snapshot: dict,
    collector: SearchResultsCollector,
    programmatic_mode: bool = False,
):
    """Create a ``web_search`` tool that instantiates a fresh engine per call."""

    @tool
    def web_search(query: str) -> str:
        """Search the web for current information, facts, or news. Returns search result snippets with source indices."""
        from local_deep_research.utilities.resource_utils import safe_close
        from local_deep_research.web_search_engines.search_engine_factory import (
            create_search_engine,
        )

        engine = create_search_engine(
            engine_name=search_engine_name,
            llm=model,
            settings_snapshot=settings_snapshot,
            programmatic_mode=programmatic_mode,
        )
        if engine is None:
            return f"Failed to create search engine '{search_engine_name}'."
        try:
            results = engine.run(query)
            if not isinstance(results, list) or not results:
                return f"No results found for '{query}'. Try rephrasing."
            start = collector.add_results(
                results, engine_name=search_engine_name
            )
            return _format_results(results, start)
        except Exception as exc:
            logger.exception("web_search tool error")
            return f"Search error: {exc}"
        finally:
            safe_close(engine, "web search engine")

    return web_search


# Fetch tool builders (full / summary_focus / summary_focus_query / disabled)
# live in ``advanced_search_system.tools.fetch``; see ``build_fetch_tool``.


def _make_specialized_search_tool(
    engine_name: str,
    description: str,
    model: BaseChatModel,
    settings_snapshot: dict,
    collector: SearchResultsCollector,
    programmatic_mode: bool = False,
):
    """Create a ``search_{engine}`` tool for a specific search engine."""

    @tool
    def specialized_search(query: str) -> str:
        """Search a specialized engine."""  # overridden below
        from local_deep_research.utilities.resource_utils import safe_close
        from local_deep_research.web_search_engines.search_engine_factory import (
            create_search_engine,
        )

        engine = create_search_engine(
            engine_name=engine_name,
            llm=model,
            settings_snapshot=settings_snapshot,
            programmatic_mode=programmatic_mode,
        )
        if engine is None:
            return f"Failed to create {engine_name} engine."
        try:
            results = engine.run(query)
            if not isinstance(results, list) or not results:
                return f"No results from {engine_name} for '{query}'. Try rephrasing."
            start = collector.add_results(results, engine_name=engine_name)
            return _format_results(results, start)
        except Exception as exc:
            logger.exception(f"search_{engine_name} tool error")
            return f"Search error ({engine_name}): {exc}"
        finally:
            safe_close(engine, f"{engine_name} search engine")

    # Override name and description after decoration
    specialized_search.name = f"search_{engine_name}"
    specialized_search.description = description
    return specialized_search


def _make_research_subtopic_tool(
    search_engine_name: str,
    model: BaseChatModel,
    settings_snapshot: dict,
    collector: SearchResultsCollector,
    max_sub_iterations: int,
    progress_callback=None,
    programmatic_mode: bool = False,
    fetch_mode: str = "summary_focus_query",
    overall_query: str = "",
):
    """Create the ``research_subtopic`` tool that spawns parallel subagents.

    ``overall_query`` is the original user query passed by the lead agent's
    strategy; it's forwarded to summary-mode fetch tools so the per-page
    extractor sees both the agent's per-fetch focus and the original
    research question.
    """

    @tool
    def research_subtopic(subtopics: list[str]) -> str:
        """Delegate parallel research on multiple subtopics. Each subtopic is
        investigated by a separate agent. Pass 2-5 focused research questions."""
        from langchain.agents import create_agent

        if not subtopics:
            return "No subtopics provided."
        if len(subtopics) > MAX_SUBTOPICS:
            subtopics = subtopics[:MAX_SUBTOPICS]

        # Emit progress for UI
        if progress_callback:
            progress_callback(
                f"Researching {len(subtopics)} subtopics in parallel",
                None,
                {
                    "phase": "sub_research",
                    "type": "milestone",
                    "subtopics": subtopics,
                },
            )

        current_date = datetime.now(UTC).strftime("%Y-%m-%d")
        subagent_prompt = (
            f"You are a focused research assistant. Today's date: {current_date}. "
            "Search thoroughly and return a concise factual summary. "
            "Reference sources by their [N] index numbers. "
            "Do NOT ask clarifying questions — provide your findings directly."
        )

        def run_subagent(topic: str) -> str:
            # Each subagent gets its own tool instances (thread safety)
            sub_web_search = _make_web_search_tool(
                search_engine_name,
                model,
                settings_snapshot,
                collector,
                programmatic_mode=programmatic_mode,
            )
            sub_tools = [sub_web_search]
            sub_fetch = build_fetch_tool(
                fetch_mode,
                collector,
                model=model,
                overall_query=overall_query,
                settings_snapshot=settings_snapshot,
            )
            if sub_fetch is not None:
                sub_tools.append(sub_fetch)
            try:
                agent = create_agent(
                    model=model,
                    tools=sub_tools,
                    system_prompt=subagent_prompt,
                )
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": topic}]},
                    {"recursion_limit": max_sub_iterations * 2 + 1},
                )
                messages = result.get("messages", [])
                if messages:
                    last = messages[-1]
                    content = getattr(last, "content", str(last))
                    if content:
                        return content
                return f"No findings for: {topic}"
            except GraphRecursionError:
                return f"Research on '{topic}' reached iteration limit. Partial findings above."
            except Exception as exc:
                logger.exception(f"Subagent failed for: {topic[:80]}")
                return f"Research on '{topic}' failed: {exc}"

        ordered_results: dict[str, str] = {}
        num_workers = min(MAX_SUBAGENT_WORKERS, len(subtopics))
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {executor.submit(run_subagent, t): t for t in subtopics}
            try:
                for future in as_completed(
                    futures, timeout=SUBAGENT_TIMEOUT_SECONDS
                ):
                    topic = futures[future]
                    try:
                        ordered_results[topic] = future.result(
                            timeout=SUBAGENT_TIMEOUT_SECONDS
                        )
                    except TimeoutError:
                        logger.warning(f"Subagent timed out for: {topic[:80]}")
                        ordered_results[topic] = (
                            f"Research on '{topic}' timed out."
                        )
                    except Exception as exc:
                        ordered_results[topic] = (
                            f"Research on '{topic}' failed: {exc}"
                        )
            except TimeoutError:
                # as_completed itself timed out — some futures didn't finish
                for future, topic in futures.items():
                    if topic not in ordered_results:
                        logger.warning(
                            f"Subagent timed out (overall): {topic[:80]}"
                        )
                        ordered_results[topic] = (
                            f"Research on '{topic}' timed out after "
                            f"{SUBAGENT_TIMEOUT_SECONDS}s."
                        )

        # Return results in original order
        parts = []
        for topic in subtopics:
            parts.append(
                f"## {topic}\n{ordered_results.get(topic, 'No results')}"
            )
        return "\n\n---\n\n".join(parts)

    return research_subtopic


# ---------------------------------------------------------------------------
# Strategy class
# ---------------------------------------------------------------------------


class LangGraphAgentStrategy(BaseSearchStrategy):
    """Research strategy using LangGraph agents with parallel subagent support.

    The lead agent autonomously decides what to search, when to dig deeper
    (via subagents), and when to synthesize — replacing the manual ReAct loop
    in the MCP strategy.
    """

    def __init__(
        self,
        model: BaseChatModel,
        search,
        citation_handler=None,
        max_iterations: int = 50,
        max_sub_iterations: int = 8,
        include_sub_research: bool = True,
        all_links_of_system: list | None = None,
        settings_snapshot: dict | None = None,
        programmatic_mode: bool = False,
        **kwargs,
    ):
        super().__init__(
            all_links_of_system=all_links_of_system,
            settings_snapshot=settings_snapshot,
            **kwargs,
        )
        self.model = model
        self.search = search
        # Whether the parent AdvancedSearchSystem is running in programmatic
        # mode (no DB metrics/rate-limit persistence). Threaded into the
        # tool factory closures so engines created per tool call inherit it.
        self.programmatic_mode = programmatic_mode
        # search.iterations (typically 1-5) controls pipeline strategies.
        # For an agent, each "iteration" is one LLM→tool round-trip, so we
        # need many more.  Treat any value below the agent minimum as "use
        # default" rather than clamping to a uselessly low number.
        self.max_iterations = (
            int(max_iterations)
            if int(max_iterations) >= MIN_ITERATIONS
            else DEFAULT_MAX_ITERATIONS
        )
        self.max_sub_iterations = int(max_sub_iterations)
        self.include_sub_research = include_sub_research
        self.citation_handler = citation_handler or CitationHandler(
            model,
            handler_type="standard",
            settings_snapshot=settings_snapshot,
        )
        self.collector = SearchResultsCollector(self.all_links_of_system)

        fetch_mode = self.get_setting(
            "search.fetch.mode", "summary_focus_query"
        )
        if fetch_mode not in FETCH_MODES:
            logger.warning(
                f"Unknown search.fetch.mode={fetch_mode!r}, falling back to "
                f"'summary_focus_query'. Valid modes: {FETCH_MODES}"
            )
            fetch_mode = "summary_focus_query"
        self.fetch_mode = fetch_mode
        logger.info(f"LangGraph agent fetch_mode={self.fetch_mode}")

        # Derive the search engine name for creating fresh instances
        self._search_engine_name = self._resolve_engine_name()

    def _resolve_engine_name(self) -> str:
        """Best-effort extraction of the configured engine name."""
        # Try settings first
        tool_setting = self.get_setting("search.tool", None)
        if tool_setting and isinstance(tool_setting, str):
            return tool_setting
        # Fall back to class name heuristic
        if self.search is not None and hasattr(self.search, "__class__"):
            name = self.search.__class__.__name__
            return name.replace("SearchEngine", "").lower()
        return "duckduckgo"

    def _get_current_engine_name(self) -> str:
        """Get the name of the currently selected search engine."""
        try:
            if hasattr(self.search, "__class__"):
                return self.search.__class__.__name__.replace(
                    "SearchEngine", ""
                ).lower()
        except Exception:
            logger.debug("Could not extract engine name from class")
        return ""

    def _build_tools(self, overall_query: str = "") -> list:
        """Build the LangChain tool list for the lead agent.

        ``overall_query`` is the original user query; it's threaded into
        summary-mode fetch tools so the per-page extractor sees both the
        agent's per-fetch focus and the original research question.
        """
        tools = []

        # Web search (always present if we have a search engine)
        if self.search is not None:
            tools.append(
                _make_web_search_tool(
                    self._search_engine_name,
                    self.model,
                    self.settings_snapshot,
                    self.collector,
                    programmatic_mode=self.programmatic_mode,
                )
            )

        # Content fetcher (returns None when fetch_mode == 'disabled')
        fetch = build_fetch_tool(
            self.fetch_mode,
            self.collector,
            model=self.model,
            overall_query=overall_query,
            settings_snapshot=self.settings_snapshot,
        )
        if fetch is not None:
            tools.append(fetch)

        # Specialized search engines
        try:
            from local_deep_research.web_search_engines.search_engines_config import (
                get_available_engines,
            )

            available = get_available_engines(
                settings_snapshot=self.settings_snapshot,
            )
            current = self._get_current_engine_name()
            for name, config in available.items():
                if name in ("auto", "meta") or name == current:
                    continue
                desc = config.get("description", f"Search using {name}")
                strengths = config.get("strengths", [])
                if strengths:
                    desc += f" Best for: {', '.join(strengths[:2])}."
                tools.append(
                    _make_specialized_search_tool(
                        name,
                        desc,
                        self.model,
                        self.settings_snapshot,
                        self.collector,
                        programmatic_mode=self.programmatic_mode,
                    )
                )
        except Exception:
            logger.warning(
                "Failed to load specialized search engines for agent tools"
            )

        # Subagent research tool
        if self.include_sub_research:
            tools.append(
                _make_research_subtopic_tool(
                    self._search_engine_name,
                    self.model,
                    self.settings_snapshot,
                    self.collector,
                    self.max_sub_iterations,
                    progress_callback=self.progress_callback,
                    programmatic_mode=self.programmatic_mode,
                    fetch_mode=self.fetch_mode,
                    overall_query=overall_query,
                )
            )

        return tools

    # -- Main entry point ---------------------------------------------------

    def analyze_topic(self, query: str) -> Dict[str, Any]:
        from langchain.agents import create_agent

        logger.info(f"LangGraph agent research: {query[:100]}")

        # Reset collector for fresh subsection call (detailed report mode)
        self.collector.reset()
        nr_of_links = len(self.all_links_of_system)

        self._update_progress(
            f'Starting agent research: "{query[:80]}"',
            5,
            {"phase": "init", "type": "milestone", "query": query[:100]},
        )
        self.check_termination()

        # Build tools (overall_query feeds summary-mode fetch tools)
        tools = self._build_tools(overall_query=query)
        if not tools:
            return self._error_result("No tools available")

        # Build system prompt — fetch_line wording mirrors the active mode
        # so the agent isn't told to use a tool that doesn't exist.
        current_date = datetime.now(UTC).strftime("%Y-%m-%d")
        if self.fetch_mode == "disabled":
            fetch_line = (
                "3. Rely on search snippets — full-page fetching is disabled "
                "for this run.\n"
            )
        elif self.fetch_mode in ("summary_focus", "summary_focus_query"):
            fetch_line = (
                "3. Use fetch_content(url, focus) when snippets aren't enough; "
                "always pass the specific question or claim you want answered "
                "as ``focus`` so the tool returns only the relevant facts.\n"
            )
        else:  # full
            fetch_line = "3. Use fetch_content to read full pages when snippets aren't enough.\n"
        # Encourage subtopic decomposition: the previous prompt only
        # said "use research_subtopic for complex multi-faceted questions"
        # which the LLM interpreted as optional, producing single-tool
        # call sequences of repeated web_search() invocations against
        # the same query (3 iterations, 0 follow-up questions, 1
        # finding). Force the multi-step path by telling the agent the
        # minimum subtopic count and making the tool call a hard
        # requirement for any non-trivial query.
        system_prompt = (
            f"You are a research assistant writing a research report. Today's date: {current_date}.\n"
            "This is NOT a chat conversation. Your only job is to research the "
            "given topic and produce a comprehensive, well-cited report.\n"
            "Do NOT ask clarifying questions, do NOT ask the user anything, "
            "do NOT offer to help further — just research and report.\n"
            "You MUST search the web before answering — never answer from memory alone.\n\n"
            "Strategy (REQUIRED — skipping any step is wrong):\n"
            "1. Start with web_search for initial exploration of the query.\n"
            "2. Decompose the query into 3-5 specific subtopics or focused "
            "research questions and call research_subtopic with all of them "
            "in a single batch to investigate each in parallel. This is "
            "REQUIRED for any non-trivial query — a single-shot web_search "
            "without subtopic follow-up is not an acceptable answer.\n"
            f"{fetch_line}"
            "4. Use search_[engine] tools for domain-specific searches "
            "(search_arxiv for science, search_pubmed for medical, etc.).\n"
            "5. When you have enough information, provide a comprehensive answer "
            "citing sources as [1], [2], etc.\n"
        )

        # Create agent — may fail if model doesn't support tool calling
        try:
            agent = create_agent(
                model=self.model,
                tools=tools,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            logger.exception("Failed to create LangGraph agent")
            return self._error_result(
                f"Failed to create agent (model may not support tool calling): {exc}"
            )

        # Stream agent execution
        effective_max = max(MIN_ITERATIONS, self.max_iterations)
        config = {"recursion_limit": effective_max * 2 + 1}
        iteration = 0
        final_content = ""
        agent_messages: list = []

        try:
            for chunk in agent.stream(
                {"messages": [{"role": "user", "content": query}]},
                config,
                stream_mode="updates",
            ):
                self.check_termination()

                if "agent" in chunk or "model" in chunk:
                    node_key = "agent" if "agent" in chunk else "model"
                    iteration += 1
                    progress = 10 + int((iteration / effective_max) * 75)
                    msgs = chunk[node_key].get("messages", [])
                    for msg in msgs:
                        if isinstance(msg, AIMessage):
                            agent_messages.append(msg)
                            content = msg.content or ""
                            tool_calls = getattr(msg, "tool_calls", [])

                            if tool_calls:
                                for tc in tool_calls:
                                    tc_args = tc.get("args", {})
                                    preview = str(
                                        tc_args.get(
                                            "query", tc_args.get("url", "")
                                        )
                                    )[:80]
                                    self._update_progress(
                                        f'Tool: {tc["name"]} — "{preview}"',
                                        min(85, progress),
                                        {
                                            "phase": "tool_call",
                                            "tool": tc["name"],
                                            "iteration": iteration,
                                        },
                                    )
                            elif content:
                                # No tool calls = final answer
                                final_content = content

                elif "tools" in chunk:
                    msgs = chunk["tools"].get("messages", [])
                    for msg in msgs:
                        tool_name = getattr(msg, "name", "tool")
                        preview = str(getattr(msg, "content", ""))[
                            :150
                        ].replace("\n", " ")
                        self._update_progress(
                            f"Result from {tool_name}: {preview}",
                            min(
                                85,
                                10 + int((iteration / effective_max) * 75) + 3,
                            ),
                            {"phase": "observation", "tool": tool_name},
                        )

        except GraphRecursionError:
            logger.warning(
                "LangGraph agent hit recursion limit, synthesizing partial results"
            )
            if not final_content:
                final_content = self._synthesize_from_collector(query)
        except ResearchTerminatedException:
            # User clicked cancel — let the termination exception
            # propagate up so the research is marked SUSPENDED (not
            # FAILED) and the thread exits cleanly.
            raise
        except Exception as exc:
            logger.exception("LangGraph agent error")
            # If the user clicked Cancel while we were in the middle of
            # an LLM call (which is now raising), the cancel flag is
            # set but the cooperative check inside progress_callback
            # never got a chance to run. Re-check it here so the user
            # gets a clean SUSPENDED state instead of a confusing
            # FAILED with the bad-model/whatever error.
            try:
                self.check_termination()
            except ResearchTerminatedException:
                raise
            if not final_content:
                if self.collector.results:
                    final_content = self._synthesize_from_collector(query)
                else:
                    return self._error_result(self._format_agent_error(exc))

        if not final_content:
            if self.collector.results:
                final_content = self._synthesize_from_collector(query)
            else:
                final_content = (
                    "Research could not produce results. Try a different query."
                )

        return self._finalize(
            query, final_content, iteration, nr_of_links, agent_messages
        )

    # -- Helpers ------------------------------------------------------------

    def _synthesize_from_collector(self, query: str) -> str:
        """Fallback synthesis when the agent was cut short."""
        results = self.collector.results
        if not results:
            return "Research could not be completed within the iteration limit."
        # Re-check the cancel flag before issuing another LLM call so
        # a user-cancelled research doesn't sit in this direct
        # ``self.model.invoke`` (which bypasses progress_callback) for
        # the full synthesis round-trip.
        try:
            self.check_termination()
        except ResearchTerminatedException:
            raise
        summaries = []
        for r in results[:20]:
            summaries.append(
                f"[{r.get('index', '?')}] {r.get('title', '')}: "
                f"{r.get('snippet', '')}"
            )
        prompt = (
            f"Synthesize a comprehensive answer to: {query}\n\n"
            f"Based on these sources:\n" + "\n".join(summaries)
        )
        try:
            response = self.model.invoke(prompt)
            return (
                response.content
                if hasattr(response, "content")
                else str(response)
            )
        except Exception as exc:
            logger.exception("Fallback synthesis failed")
            return f"Research collected {len(results)} sources but synthesis failed: {exc}"

    def _ensure_images_for_results(
        self, all_search_results: list, allowed_urls: set[str] | None = None
    ) -> None:
        """Ensure URLs have html_content for the report-stage image enhancer.

        The langgraph agent may decide not to invoke the ``fetch_content`` tool,
        leaving ``search_results[].html_content`` empty — which causes the
        report to be text-only even when ``report.enable_images`` is on. This
        method proactively fetches images for every URL that doesn't yet have
        ``html_content``, so image extraction is independent of the agent's
        tool-call decisions.

        ``allowed_urls`` constrains the fetch to URLs the caller has decided
        are worth scraping. Pass ``None`` to allow every URL (the legacy
        behaviour); pass an empty set to skip fetching entirely; pass a
        populated set to fetch only URLs in that set. The set is compared
        as-is — callers should pre-normalise (e.g. drop trailing slash,
        lower-case host) when they care about formatting equivalence.
        """
        if not get_bool_setting_from_snapshot(
            "report.enable_images",
            default=False,
            settings_snapshot=self.settings_snapshot,
        ):
            logger.info(
                "[IMG-TRACE] langgraph auto-image-fill: skipped (report.enable_images=off)"
            )
            return

        # An explicit empty allowlist means the caller has decided no URL
        # is worth fetching. Distinct from "no allowlist given" (= legacy
        # behaviour, fetch everything missing html_content).
        if allowed_urls is not None and not allowed_urls:
            logger.info(
                "[IMG-TRACE] langgraph auto-image-fill: skipped (allowed_urls is empty)"
            )
            return

        urls_to_fetch = _select_urls_to_fetch(all_search_results, allowed_urls)

        if not urls_to_fetch:
            logger.info(
                "[IMG-TRACE] langgraph auto-image-fill: skipped (no source missing html_content after allowlist)"
            )
            return

        logger.info(
            f"[IMG-TRACE] langgraph auto-image-fill: fetching {len(urls_to_fetch)} URLs for image extraction"
        )
        try:
            from local_deep_research.images.serialize import dumps_images
            from local_deep_research.research_library.downloaders.extraction.pipeline import (
                fetch_content_with_images,
            )

            titles_attr = getattr(self, "titles", None)
            titles = titles_attr if isinstance(titles_attr, dict) else {}

            data = fetch_content_with_images(
                urls_to_fetch,
                titles={u: titles.get(u, "") for u in urls_to_fetch},
                settings_snapshot=self.settings_snapshot,
            )
        except Exception:
            logger.exception(
                "langgraph auto-image-fill: fetch_content_with_images failed"
            )
            return

        filled = 0
        for url in urls_to_fetch:
            entry = data.get(url) if isinstance(data, dict) else None
            images = entry.get("images", []) if entry else []
            # Per-image LANGGRAPH_FILL event: lets a log consumer see
            # the (alt, img_url, source_url) of every image the langgraph
            # fetch surfaced, not just the page-level count. cite_num
            # and ref_url are unknown at this stage (the image hasn't
            # been bound to a citation yet) and are emitted as ``-``
            # so the key set is uniform across the entire IMG-TRACE
            # schema. One ``grep IMG-TRACE.*LANGGRAPH_FILLED_IMG``
            # over the log reconstructs the complete alt inventory of
            # the pre-pipeline image-fill phase.
            for img in images:
                logger.info(
                    f"[IMG-TRACE] LANGGRAPH_FILLED_IMG src_url={url} "
                    f"img_alt={(getattr(img, 'alt', '') or '')[:200]!r} "
                    f"img_url={getattr(img, 'url', '')} "
                    f"img_source_url={getattr(img, 'source_url', '')} "
                    f"cite_num=- ref_url=-"
                )
            payload = dumps_images(images)
            updated = self.collector.attach_html_content(url, payload)
            # Event 4 (closes G1): ATTACH_HTML_CONTENT — definitive
            # trace of every html_content write. Aug 6 had 0 hits for
            # attach_html_content because _ensure_images_for_results
            # had no caller. This event makes any future call
            # observable.
            logger.info(
                f"[IMG-TRACE] ATTACH_HTML_CONTENT research={research_id} "
                f"url={url} updated={updated} "
                f"prev_len=0 new_len={len(payload) if updated else 0}"
            )
            if updated:
                filled += 1
        logger.info(
            f"[IMG-TRACE] LANGGRAPH_FILL filled={filled}/{len(urls_to_fetch)}"
        )

    def _finalize(
        self,
        query: str,
        final_answer: str,
        iteration: int,
        nr_of_links: int,
        agent_messages: list,
    ) -> Dict[str, Any]:
        """Apply citation handling and build the return dict."""
        self._update_progress(
            f"Synthesizing {len(self.collector.results)} sources with citations",
            90,
            {"phase": "synthesis", "type": "milestone"},
        )

        all_search_results = self.collector.results
        synthesized_content = final_answer
        documents: list = []

        # Image fetch is deferred until after the Sources block is
        # built (see below) so the allowlist can be derived from the
        # URLs the LLM actually chose to cite — the previous behaviour
        # of "fetch every URL missing html_content" was wasteful and
        # scraped pages the report never referenced.

        # Citation handling — only if we have results
        if all_search_results:
            try:
                citation_result = self.citation_handler.analyze_followup(
                    query,
                    all_search_results,
                    previous_knowledge=final_answer,
                    nr_of_links=nr_of_links,
                )
                if isinstance(citation_result, dict):
                    synthesized_content = citation_result.get(
                        "content", citation_result.get("response", final_answer)
                    )
                    documents = citation_result.get("documents", [])
            except Exception:
                logger.warning(
                    "Citation handler failed, using raw agent answer"
                )

        # Format sources
        formatted_output = synthesized_content
        if all_search_results:
            try:
                all_links = extract_links_from_search_results(
                    all_search_results
                )
                if all_links:
                    sources_md = format_links_to_markdown(all_links)
                    if sources_md:
                        formatted_output = f"{synthesized_content}\n\n## Sources\n\n{sources_md}"
            except Exception:
                logger.exception("Failed to format source links")

        # Image fetch has been removed from the langgraph agent loop.
        # The previous behaviour called ``_ensure_images_for_results``
        # once per LLM reasoning round (the agent loop ran 22 rounds
        # for the Shanghai 2026-08-03 study, scraping ~80-200 pages
        # per round and dominating the 7-hour research walltime with
        # Playwright rendering). The langgraph agent loop now fetches
        # text only; the image fill is a single post-loop pass done by
        # ``research_service._deferred_image_fill`` after the markdown
        # report + sources block are finalised, just before
        # ``enhance_report_with_images`` runs. ``postprocessing`` then
        # reads the populated ``html_content`` exactly as it did
        # before. No langgraph-side code path remains — if you see a
        # ``[IMG-TRACE] langgraph auto-image-fill`` event in any log
        # it is from a pre-refactor run. See
        # ``research_service._deferred_image_fill`` for the current
        # single-pass implementation.
        logger.info(
            "image fetch deferred to post-finalize pass; "
            "see research_service._deferred_image_fill"
        )

        # Build reasoning trace from agent messages
        reasoning_trace = []
        for msg in agent_messages:
            entry: Dict[str, Any] = {"role": "assistant"}
            if hasattr(msg, "content") and msg.content:
                entry["content"] = msg.content
            tool_calls = getattr(msg, "tool_calls", [])
            if tool_calls:
                entry["tool_calls"] = [
                    {"name": tc.get("name"), "args": tc.get("args", {})}
                    for tc in tool_calls
                ]
            reasoning_trace.append(entry)

        self._update_progress(
            "Research complete",
            100,
            {"phase": "complete", "type": "milestone", "iterations": iteration},
        )

        return {
            "findings": [
                {
                    "content": synthesized_content,
                    "question": query,
                    "search_results": all_search_results,
                    "documents": documents,
                }
            ],
            "iterations": iteration,
            "questions": {},
            "formatted_findings": formatted_output,
            "current_knowledge": synthesized_content,
            "sources": list(set(self.collector.sources)),
            "search_results": all_search_results,
            "documents": documents,
            "reasoning_trace": reasoning_trace,
            "error": None,
        }

    @staticmethod
    def _format_agent_error(exc: BaseException) -> str:
        """Prefix the exception type so downstream rendering (and the
        `ErrorReportGenerator` pattern map) have a consistent shape to match
        on. The bare `str(exc)` produced by the catch-all loses the type,
        which makes deep LangChain / LangGraph failures hard to recognise.
        """
        return f"Agent error: {type(exc).__name__}: {exc}"

    def _error_result(self, error: str) -> Dict[str, Any]:
        logger.error(f"LangGraph agent strategy error: {error}")
        self._update_progress(
            f"Error: {error}",
            100,
            {"phase": "error", "error": error, "status": "failed"},
        )
        return {
            "findings": [],
            "iterations": 0,
            "questions": {},
            "formatted_findings": f"Error: {error}",
            "current_knowledge": "",
            "sources": [],
            "search_results": [],
            "documents": [],
            "reasoning_trace": [],
            "error": error,
        }

    def close(self):
        """No persistent resources to clean up."""
        pass

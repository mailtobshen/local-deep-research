"""Agent-facing ``fetch_content`` tool builders.

Public API:
    FETCH_MODES         — tuple of valid mode strings.
    build_fetch_tool()  — returns a LangChain ``@tool`` (or ``None`` when
                          mode == "disabled" so the caller can skip
                          registration).

Modes:
    disabled              — fetch tool is not registered with the agent.
    full                  — return the full extracted page text (legacy
                            behavior; can flood small-model context with
                            boilerplate / metadata enrichment).
    summary_focus         — LLM extracts only spans relevant to a focus
                            question the agent supplies per call.
    summary_focus_query   — same as above, but the prompt also includes
                            the original research query (passed in
                            programmatically by the strategy) so the
                            extractor can disambiguate vague focuses.

Each tool registers fetched URLs in the strategy's
``SearchResultsCollector`` for citation tracking, returning the result as
``[N] Title: ...\\nURL: ...\\n\\n<body>`` exactly like the original
in-strategy implementation, so downstream prompt formatting is unchanged.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from loguru import logger

from local_deep_research.utilities.js_rendering import (
    read_js_rendering_setting as _read_js_rendering_setting,
)

from .prompts import SUMMARY_FOCUS_PROMPT, SUMMARY_FOCUS_QUERY_PROMPT


# Per-call timeouts and caps. Kept here rather than in the strategy file
# because they are properties of the fetch tool, not of agent
# orchestration.
CONTENT_FETCH_TIMEOUT = 30
CONTENT_MAX_LENGTH = 10_000

FETCH_MODES = (
    "disabled",
    "full",
    "summary_focus",
    "summary_focus_query",
)


def _register_in_collector(
    collector: Any,
    url: str,
    title: str,
    snippet_source: str,
) -> int:
    """Register a fetched URL in the collector and return its 1-based citation index.

    If the URL was already tracked (via a prior search hit) the existing
    index is reused so the agent sees a stable citation per URL.
    """
    existing_idx = collector.find_by_url(url)
    if existing_idx is not None:
        return existing_idx
    snippet = snippet_source[:200].strip()
    if len(snippet_source) > 200:
        snippet += "..."
    start = collector.add_results(
        [{"title": title, "link": url, "snippet": snippet}],
        engine_name="fetch",
    )
    return start + 1


# Image fetching was historically triggered inside the fetch_content
# tool (immediate). After fix #5 in
# docs/superpowers/plans/2026-08-05-image-chain-9-fixes.md, all image
# fetching is unified into the post-report _deferred_image_fill pass.
# The langgraph fetch tool now only returns text/URL — image attach
# would have been a no-op anyway since the LLM never sees the
# returned image data. See _deferred_image_fill in
# web/services/research_service.py for the unified path.


def _enforce_url_isolation(
    url: str, settings_snapshot: dict | None
) -> str | None:
    """Reject non-.onion URLs at the fetcher boundary, but only under tor mode.

    The activation condition is the *primary engine's* network. If the
    user picked a clearnet engine (google, bing, …) the isolation
    policy does not apply and any URL is allowed — the agent is doing
    regular research. Only when the chosen primary engine is tor-egress
    does the URL-level check kick in.

    Inside tor mode the URL itself is the only authority: ``.onion`` is
    the Tor reserved TLD and no clearnet domain can ever end in
    ``.onion``. We refuse any URL that fails this check, which closes
    two distinct failure modes in one rule:

    1. **Race-condition safety.** Earlier policy checked the primary
       engine's network and only logged a warning. That let a clearnet
       URL slip through whenever the session engine and the URL
       diverged (e.g. a Tor-search result that linked out, or an LLM
       call coming back after the engine was swapped). The check is
       now local and stateless — once we are inside tor mode, no
       engine snapshot to drift, no flag to forget to set.

    2. **LLM URL fabrication defence.** If the LLM invents a
       plausible-looking clearnet URL that never existed, the fetcher
       used to happily attempt the request. Rejecting on URL shape
       alone means a fabricated URL is short-circuited before any DNS /
       TCP work, and the agent sees a deterministic refusal it can
       reason about.

    Per the user's choice, ``.onion`` URLs are always allowed in tor
    mode. URLs embedded inside a fetched ``.onion`` page (the page
    source contains a ``https://...`` reference) are *not* fetched by
    this tool — the tool only fetches the single URL the agent
    passes; it does not follow page links.

    Args:
        url: The URL the agent is about to fetch.
        settings_snapshot: Thread-safe settings snapshot (used to read
            the chosen primary engine and its network).

    Returns:
        ``None`` if the URL is allowed (either because we are not in
        tor mode, or because the URL is ``.onion``). A fixed refusal
        string otherwise — the caller should return it directly to
        the agent without invoking ``ContentFetcher``.
    """
    # ----- Gate 1: only enforce isolation under tor mode. -----
    if not settings_snapshot:
        # Without a snapshot we cannot know the primary engine. Be
        # safe: treat as not-tor and allow, matching the pre-fix
        # behaviour for environments where the snapshot was not
        # threaded through.
        return None
    try:
        from local_deep_research.web_search_engines.search_engines_config import (
            get_setting_from_snapshot,
            get_engine_network,
        )
    except Exception:
        logger.exception(
            "[IMG-TRACE] FETCH_CONTENT_BLOCKED "
            f"url={url} reason=clearnet_url "
            "detail=engine_network_import_failed"
        )
        return (
            f"[FETCH BLOCKED] clearnet URL {url!r} rejected: URL "
            "isolation check could not run (engine-network helper "
            "import failed); refusing the fetch to preserve isolation."
        )
    try:
        current_tool = get_setting_from_snapshot(
            "search.tool", None, settings_snapshot=settings_snapshot
        )
    except Exception:
        current_tool = None
    if not current_tool:
        return None
    try:
        if get_engine_network(current_tool, settings_snapshot) != "tor":
            return None  # Clearnet primary engine: no isolation.
    except Exception:
        # If we cannot resolve the engine's network, be conservative
        # and skip the check rather than blocking every fetch — the
        # alternative would be a hard outage for users whose engine
        # config drifted.
        return None

    # ----- Gate 2: in tor mode, accept only .onion URLs. -----
    try:
        from local_deep_research.utilities.is_darkweb_url import (
            is_darkweb_url,
        )
    except Exception:
        logger.exception(
            "[IMG-TRACE] FETCH_CONTENT_BLOCKED "
            f"url={url} reason=clearnet_url "
            "detail=is_darkweb_url_import_failed"
        )
        return (
            f"[FETCH BLOCKED] clearnet URL {url!r} rejected: URL "
            "isolation check could not run (is_darkweb_url import "
            "failed); refusing the fetch to preserve isolation."
        )
    if is_darkweb_url(url):
        return None  # .onion URL is expected; allow.
    logger.warning(
        "[IMG-TRACE] FETCH_CONTENT_BLOCKED "
        f"url={url} reason=clearnet_url "
        f"primary_engine={current_tool}"
    )
    return (
        f"[FETCH BLOCKED] clearnet URL {url!r} rejected: this research "
        "session isolates to .onion sources only because the primary "
        f"engine {current_tool!r} is tor-egress. .onion URLs (and only "
        ".onion URLs) can be fetched. If the agent surfaced this URL "
        "from a fetched .onion page, treat its content as unverified "
        "— do not follow the link."
    )


def _make_full_fetch_tool(
    collector: Any, settings_snapshot: dict | None = None
):
    @tool
    def fetch_content(url: str) -> str:
        """Download and read the full text content from a URL. Use when search snippets aren't detailed enough.

        Only ``.onion`` URLs are allowed; clearnet URLs are rejected at
        the tool boundary (see ``_enforce_url_isolation``).
        """
        # Reject clearnet URLs before importing ``ContentFetcher``: if the
        # fetcher module fails to load (e.g. a transient dependency
        # error), the isolation check must still hold — refusing a
        # clearnet URL is the safer default than letting it leak through.
        blocked = _enforce_url_isolation(url, settings_snapshot)
        if blocked is not None:
            return blocked
        from local_deep_research.content_fetcher import ContentFetcher

        enable_js = _read_js_rendering_setting(settings_snapshot)
        try:
            with ContentFetcher(
                timeout=CONTENT_FETCH_TIMEOUT,
                enable_js_rendering=enable_js,
            ) as fetcher:
                result = fetcher.fetch(url, max_length=CONTENT_MAX_LENGTH)
                if result.get("status") == "success":
                    title = result.get("title", "")
                    content = result.get("content", "")
                    cite_idx = _register_in_collector(
                        collector, url, title, content
                    )
                    # Event 3 (closes G5): FETCH_CONTENT_TOOL_CALL —
                    # definitive evidence whether LLM called
                    # fetch_content during the run. research_id is not
                    # in scope at the tool layer; loguru's
                    # research_id patcher injects it on emission.
                    logger.info(
                        "[IMG-TRACE] FETCH_CONTENT_TOOL_CALL "
                        f"url={url} mode=full "
                        f"result_status=success html_len={len(content)}"
                    )
                    # Image extraction moved entirely to the
                    # post-report _deferred_image_fill pass (fix #5).
                    return (
                        f"[{cite_idx}] Title: {title}\nURL: {url}\n\n{content}"
                    )
                return f"Failed to fetch {url}: {result.get('error', 'unknown error')}"
        except Exception as exc:
            logger.exception("fetch_content tool error")
            return f"Error fetching {url}: {exc}"

    return fetch_content


def _make_summary_fetch_tool(
    collector: Any,
    model: BaseChatModel,
    overall_query: str | None,
    settings_snapshot: dict | None = None,
):
    """Build the summary-mode fetch tool.

    overall_query=None → focus-only prompt (``summary_focus`` mode).
    overall_query=str  → focus + overall-query prompt (``summary_focus_query``).
    """
    use_query = bool(overall_query)
    template = SUMMARY_FOCUS_QUERY_PROMPT if use_query else SUMMARY_FOCUS_PROMPT

    mode_label = "summary_focus_query" if use_query else "summary_focus"

    @tool
    def fetch_content(url: str, focus: str) -> str:
        """Fetch a URL and return only the spans of text relevant to ``focus``.
        Pass the specific question or claim you want answered as ``focus`` — the
        tool will quote relevant facts verbatim and discard unrelated content.

        Only ``.onion`` URLs are allowed; clearnet URLs are rejected at
        the tool boundary (see ``_enforce_url_isolation``).
        """
        # See the full-mode variant for why the isolation check runs
        # before importing ``ContentFetcher``.
        blocked = _enforce_url_isolation(url, settings_snapshot)
        if blocked is not None:
            return blocked
        from local_deep_research.content_fetcher import ContentFetcher

        enable_js = _read_js_rendering_setting(settings_snapshot)
        try:
            with ContentFetcher(
                timeout=CONTENT_FETCH_TIMEOUT,
                enable_js_rendering=enable_js,
            ) as fetcher:
                result = fetcher.fetch(url, max_length=CONTENT_MAX_LENGTH)
                if result.get("status") != "success":
                    return f"Failed to fetch {url}: {result.get('error', 'unknown error')}"

                title = result.get("title", "")
                content = result.get("content", "")

                fmt_kwargs = {
                    "focus": focus,
                    "title": title,
                    "url": url,
                    "content": content,
                }
                if use_query:
                    fmt_kwargs["overall_query"] = overall_query
                prompt = template.format(**fmt_kwargs)

                try:
                    summary_msg = model.invoke(prompt)
                    summary = getattr(
                        summary_msg, "content", str(summary_msg)
                    ).strip()
                except Exception as exc:
                    logger.exception("fetch_content summary LLM error")
                    return f"Error summarizing {url}: {exc}"

                # Diagnostic log: per-fetch input/output for evaluating the
                # summariser. Single multi-line block so it's atomic per call
                # and easy to grep with ``grep -A1000 "[FETCH] mode="``.
                log_lines = [
                    f"[FETCH] mode={mode_label} url={url}",
                    f"[FETCH] focus: {focus}",
                ]
                if use_query:
                    log_lines.append(f"[FETCH] overall_query: {overall_query}")
                log_lines.extend(
                    [
                        f"[FETCH] title: {title}",
                        f"[FETCH] page_text ({len(content)} chars):",
                        content,
                        f"[FETCH] summary returned ({len(summary)} chars):",
                        summary or "(empty)",
                        "[FETCH] ---",
                    ]
                )
                logger.info("\n".join(log_lines))

                cite_idx = _register_in_collector(
                    collector, url, title, summary or content
                )
                # Event 3 (closes G5): FETCH_CONTENT_TOOL_CALL —
                # summary mode variant. research_id injected by loguru
                # patcher.
                logger.info(
                    "[IMG-TRACE] FETCH_CONTENT_TOOL_CALL "
                    f"url={url} mode=summary "
                    f"result_status=success html_len={len(summary or content)}"
                )
                # Image extraction moved entirely to the
                # post-report _deferred_image_fill pass (fix #5).
                return f"[{cite_idx}] Title: {title}\nURL: {url}\n\n{summary}"
        except Exception as exc:
            logger.exception("fetch_content tool error")
            return f"Error fetching {url}: {exc}"

    return fetch_content


def build_fetch_tool(
    mode: str,
    collector: Any,
    *,
    model: BaseChatModel | None = None,
    overall_query: str = "",
    settings_snapshot: dict | None = None,
):
    """Build the agent-facing ``fetch_content`` tool for *mode*.

    Returns ``None`` when ``mode == 'disabled'``; the caller should not
    register the tool with the agent in that case (and the system prompt
    should also drop the corresponding instruction line so the agent
    isn't told to use a tool that doesn't exist).

    ``settings_snapshot`` is captured by the tool closure so the per-call
    JS-rendering toggle can be read on a worker thread (where
    ``threading.local`` context does not propagate).
    """
    if mode == "disabled":
        return None
    if mode == "full":
        return _make_full_fetch_tool(
            collector, settings_snapshot=settings_snapshot
        )
    if mode == "summary_focus":
        if model is None:
            raise ValueError("summary_focus fetch mode requires a model")
        return _make_summary_fetch_tool(
            collector,
            model,
            overall_query=None,
            settings_snapshot=settings_snapshot,
        )
    if mode == "summary_focus_query":
        if model is None:
            raise ValueError("summary_focus_query fetch mode requires a model")
        # Empty overall_query falls back to focus-only behaviour at format
        # time; we keep the *_query mode label so logs stay diagnostic.
        return _make_summary_fetch_tool(
            collector,
            model,
            overall_query=overall_query or None,
            settings_snapshot=settings_snapshot,
        )
    raise ValueError(
        f"Unknown fetch mode {mode!r}; expected one of {FETCH_MODES}"
    )


__all__ = ["FETCH_MODES", "build_fetch_tool"]

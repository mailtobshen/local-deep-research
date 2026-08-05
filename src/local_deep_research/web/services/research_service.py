import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path

from loguru import logger

from ...exceptions import DuplicateResearchError, ResearchTerminatedException
from ...config.llm_config import get_llm
from ...settings.manager import SnapshotSettingsContext

# Output directory for research results
from ...config.paths import get_research_outputs_directory
from ...config.search_config import get_search
from ...constants import ResearchStatus
from ...database.models import ResearchHistory, ResearchStrategy
from ...database.session_context import get_user_db_session
from ...database.thread_local_session import thread_cleanup


# Setting keys whose container-injected LDR_* env values should be folded
# into the in-research settings snapshot. Mirrors ``_ENV_FOR`` in
# ``scripts/check_engines.py`` — the CLI that this in-research pre-flight
# duplicates for live runs. Keys whose DB value is set take precedence.
_ENV_SNAPSHOT_KEYS = {
    "search.engine.web.searxng.default_params.instance_url": (
        "LDR_SEARCH_ENGINE_WEB_SEARXNG_DEFAULT_PARAMS_INSTANCE_URL",
    ),
    "search.engine.web.firecrawl.enable": (
        "LDR_SEARCH_ENGINE_WEB_FIRECRAWL_ENABLE",
    ),
    "search.engine.web.firecrawl.api_url": (
        "LDR_SEARCH_ENGINE_WEB_FIRECRAWL_API_URL",
    ),
    "search.engine.web.firecrawl.api_key": (
        "LDR_SEARCH_ENGINE_WEB_FIRECRAWL_API_KEY",
    ),
}


@contextmanager
def _perf_stage(research_id: str, stage: str):
    """Emit ``[PERF]`` begin/end with ``elapsed_s`` for a research sub-stage.

    Use as ``with _perf_stage(research_id, "analyze_topic"): ...`` so the
    end event carries the wall-time spent in the block (even when an
    exception is raised — the begin/end pair is balanced via the context
    manager's finally path).

    The ``[PERF]`` namespace is deliberately separate from ``[IMG-TRACE]``
    so image-pipeline grep filters stay clean. See img-trace-observability.
    """
    start = time.monotonic()
    logger.info(
        f"[PERF] research={research_id} stage={stage} event=begin "
        f"tid={threading.get_ident()}"
    )
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        logger.info(
            f"[PERF] research={research_id} stage={stage} event=end "
            f"elapsed_s={elapsed:.3f}"
        )


def _merge_env_into_snapshot(snapshot: dict) -> dict:
    """Return a shallow copy of ``snapshot`` with empty values filled from
    ``LDR_*`` env vars for keys that ``scripts/check_engines.py`` also
    honours. Existing (non-empty) DB values are never overwritten.
    """
    merged = dict(snapshot)
    for key, env_vars in _ENV_SNAPSHOT_KEYS.items():
        existing = merged.get(key)
        # Snapshot values can be raw or wrapped as {"value": ...}.
        if isinstance(existing, dict) and "value" in existing:
            existing = existing["value"]
        if existing not in (None, "", {}):
            continue
        for env_var in env_vars:
            env_val = os.getenv(env_var)
            if env_val in (None, ""):
                continue
            merged[key] = (
                str(env_val).lower() in ("1", "true", "yes", "on")
                if key.endswith(".enable")
                else env_val
            )
            break
    return merged
from ..translations import _
from ...error_handling.openai_compat_errors import (
    friendly_openai_compatible_error,
    is_openai_compat_runtime_error,
)
from ...error_handling.report_generator import ErrorReportGenerator
from ...utilities.thread_context import set_search_context
from ...report_generator import IntegratedReportGenerator
from ...search_system import AdvancedSearchSystem
from ...text_optimization import CitationFormatter, CitationMode
from ...utilities.log_utils import log_for_research
from ...utilities.search_utilities import extract_links_from_search_results
from ...utilities.threading_utils import thread_context, thread_with_app_context
from ..models.database import calculate_duration
from ...settings.env_registry import get_env_setting
from .socket_service import SocketIOService

OUTPUT_DIR = get_research_outputs_directory()


# Global concurrent research limit (server-wide, across all users)
_MAX_GLOBAL_CONCURRENT = get_env_setting(
    "server.max_concurrent_research", default=10
)
_global_research_semaphore = threading.Semaphore(_MAX_GLOBAL_CONCURRENT)

# Socket.IO emission throttling: minimum interval between progress emissions per research
_EMIT_THROTTLE_SECONDS = 0.2  # 200ms
_EMIT_TTL_SECONDS = 3600  # 1 hour — evict stale entries from orphaned research
_emit_cleanup_counter = 0
_last_emit_times: dict[str, float] = {}
_last_emit_lock = threading.Lock()


def _parse_research_metadata(research_meta) -> dict:
    """Parse research_meta into a dict, handling both dict and JSON string types."""
    if isinstance(research_meta, dict):
        return dict(research_meta)
    if isinstance(research_meta, str):
        try:
            parsed = json.loads(research_meta)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.exception("Failed to parse research_meta as JSON")
            return {}
    return {}


def get_citation_formatter():
    """Get citation formatter with settings from thread context."""
    # Import here to avoid circular imports
    from ...config.search_config import get_setting_from_snapshot

    citation_format = get_setting_from_snapshot(
        "report.citation_format", "number_hyperlinks"
    )
    mode_map = {
        "number_hyperlinks": CitationMode.NUMBER_HYPERLINKS,
        "domain_hyperlinks": CitationMode.DOMAIN_HYPERLINKS,
        "domain_id_hyperlinks": CitationMode.DOMAIN_ID_HYPERLINKS,
        "domain_id_always_hyperlinks": CitationMode.DOMAIN_ID_ALWAYS_HYPERLINKS,
        "source_tagged_hyperlinks": CitationMode.SOURCE_TAGGED_HYPERLINKS,
        "no_hyperlinks": CitationMode.NO_HYPERLINKS,
    }
    mode = mode_map.get(citation_format, CitationMode.NUMBER_HYPERLINKS)
    return CitationFormatter(mode=mode)


def export_report_to_memory(
    markdown_content: str, format: str, title: str | None = None
):
    """
    Export a markdown report to different formats in memory.

    Uses the modular exporter registry to support multiple formats.
    Available formats can be queried with ExporterRegistry.get_available_formats().

    Args:
        markdown_content: The markdown content to export
        format: Export format (e.g., 'pdf', 'odt', 'latex', 'quarto', 'ris')
        title: Optional title for the document

    Returns:
        Tuple of (content_bytes, filename, mimetype)
    """
    from ...exporters import ExporterRegistry, ExportOptions

    # Normalize format
    format_lower = format.lower()

    # Get exporter from registry
    exporter = ExporterRegistry.get_exporter(format_lower)

    if exporter is None:
        available = ExporterRegistry.get_available_formats()
        raise ValueError(
            f"Unsupported export format: {format}. "
            f"Available formats: {', '.join(available)}"
        )

    # Title prepending is now handled by each exporter via _prepend_title_if_needed()
    # PDF and ODT exporters prepend titles; RIS and other formats ignore them

    # Create options
    options = ExportOptions(title=title)

    # Export
    result = exporter.export(markdown_content, options)

    logger.info(
        f"Generated {format_lower} in memory, size: {len(result.content)} bytes"
    )

    return result.content, result.filename, result.mimetype


def save_research_strategy(research_id, strategy_name, username=None):
    """
    Save the strategy used for a research to the database.

    Args:
        research_id: The ID of the research
        strategy_name: The name of the strategy used
        username: The username for database access (required for thread context)
    """
    try:
        logger.debug(
            f"save_research_strategy called with research_id={research_id}, strategy_name={strategy_name}"
        )
        with get_user_db_session(username) as session:
            # Check if a strategy already exists for this research
            existing_strategy = (
                session.query(ResearchStrategy)
                .filter_by(research_id=research_id)
                .first()
            )

            if existing_strategy:
                # Update existing strategy
                existing_strategy.strategy_name = strategy_name
                logger.debug(
                    f"Updating existing strategy for research {research_id}"
                )
            else:
                # Create new strategy record
                new_strategy = ResearchStrategy(
                    research_id=research_id, strategy_name=strategy_name
                )
                session.add(new_strategy)
                logger.debug(
                    f"Creating new strategy record for research {research_id}"
                )

            session.commit()
            logger.info(
                f"Saved strategy '{strategy_name}' for research {research_id}"
            )
    except Exception:
        logger.exception("Error saving research strategy")


def get_research_strategy(research_id, username=None):
    """
    Get the strategy used for a research.

    Args:
        research_id: The ID of the research
        username: The username for database access (required for thread context)

    Returns:
        str: The strategy name or None if not found
    """
    try:
        with get_user_db_session(username) as session:
            strategy = (
                session.query(ResearchStrategy)
                .filter_by(research_id=research_id)
                .first()
            )

            return strategy.strategy_name if strategy else None
    except Exception:
        logger.exception("Error getting research strategy")
        return None


def start_research_process(
    research_id,
    query,
    mode,
    run_research_callback,
    **kwargs,
):
    """
    Start a research process in a background thread.

    Args:
        research_id: The ID of the research
        query: The research query
        mode: The research mode (quick/detailed)
        run_research_callback: The callback function to run the research
        **kwargs: Additional parameters to pass to the research process (model, search_engine, etc.)

    Returns:
        threading.Thread: The thread running the research
    """
    from ..routes.globals import check_and_start_research

    # Pass the app context to the thread.
    run_research_callback = thread_with_app_context(run_research_callback)

    # Wrap callback with global concurrency limiter
    original_callback = run_research_callback

    def _rate_limited_callback(*args, **kw):
        _global_research_semaphore.acquire()
        try:
            return original_callback(*args, **kw)
        finally:
            _global_research_semaphore.release()

    # Prepare (but do not start) the background thread.
    thread = threading.Thread(
        target=_rate_limited_callback,
        args=(
            thread_context(),
            research_id,
            query,
            mode,
        ),
        kwargs=kwargs,
    )
    thread.daemon = True

    # Atomic check-and-start: refuses to spawn a second live thread
    # for the same research_id. Guards against the double-spawn window
    # where a post-spawn commit failure in the queue processor could
    # otherwise cause the retry loop to dispatch the same research twice.
    started = check_and_start_research(
        research_id,
        {
            "thread": thread,
            "progress": 0,
            "status": ResearchStatus.IN_PROGRESS,
            "log": [],
            "settings": kwargs,
        },
    )
    if not started:
        raise DuplicateResearchError(
            f"Research {research_id} already has a live thread"
        )

    return thread


def _generate_report_path(query: str) -> Path:
    """
    Generates a path for a new report file based on the query.

    Args:
        query: The query used for the report.

    Returns:
        The path that it generated.

    """
    # Generate a unique filename that does not contain
    # non-alphanumeric characters.
    query_hash = hashlib.md5(  # DevSkim: ignore DS126858
        query.encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:10]
    return OUTPUT_DIR / (
        f"research_report_{query_hash}_{int(datetime.now(UTC).timestamp())}.md"
    )


@contextmanager
def _open_image_enhancer_session(username, settings_snapshot):
    """Read report.image_* settings, build a Firecrawl client, open DB session.

    Yields (args, db_session) where args is a dict unpackable into
    ``enhance_report_with_images(**)``. The DB session is opened for the
    duration of the with-block. Any Firecrawl client construction error
    is downgraded to a debug log and ``firecrawl_client`` is yielded as
    ``None`` — the caller decides whether to fail or fall back.
    """
    from ...config.thread_settings import get_setting_from_snapshot
    from ...research_library.downloaders.extraction import (
        pipeline as extract_pipeline,
    )

    enable_images = get_setting_from_snapshot(
        "report.enable_images", False, settings_snapshot=settings_snapshot,
    )
    vision_model = get_setting_from_snapshot(
        "report.image_vision_model", "", settings_snapshot=settings_snapshot,
    )
    vision_url = get_setting_from_snapshot(
        "report.image_vision_url", "", settings_snapshot=settings_snapshot,
    )
    vision_key = get_setting_from_snapshot(
        "report.image_vision_api_key", "", settings_snapshot=settings_snapshot,
    )
    vision_min_alt_count = get_setting_from_snapshot(
        "report.image_vision_min_alt_count", 3,
        settings_snapshot=settings_snapshot,
    )
    vision_cap = get_setting_from_snapshot(
        "report.image_vision_cap", 10, settings_snapshot=settings_snapshot,
    )
    # Backward compat: if URL empty but model set, fall back to the main
    # Ollama endpoint — matches the inline behaviour in quick branch.
    if vision_model and not vision_url:
        vision_url = get_setting_from_snapshot(
            "llm.ollama.url", "http://localhost:11434",
            settings_snapshot=settings_snapshot,
        )
    firecrawl_client = None
    try:
        firecrawl_client = (
            extract_pipeline._new_firecrawl_client_from_snapshot(
                settings_snapshot
            )
        )
    except Exception:
        logger.debug(
            "Firecrawl client unavailable for image persist fallback",
            exc_info=True,
        )

    args = dict(
        vision_model=vision_model,
        vision_url=vision_url or None,
        vision_api_key=vision_key or None,
        vision_min_alt_count=vision_min_alt_count,
        vision_cap=vision_cap,
        firecrawl_client=firecrawl_client,
        enable_images=enable_images,
    )
    with get_user_db_session(username) as db_session:
        yield args, db_session


def _inject_all_links_of_system(
    results: dict, system: object | None
) -> dict:
    """Return a shallow copy of ``results`` with ``all_links_of_system`` merged.

    In detailed mode, the deferred pass only sees the LAST subsection's
    ``search_results[]`` because ``langgraph_agent_strategy.collector.reset()``
    clears ``_results`` between subsections. ``all_links_of_system`` is the
    cross-subsection cumulative list that survives ``reset()``. Without it,
    images extracted by ``_attach_images_if_enabled`` during earlier
    subsections never reach ``build_citation_index`` and the deferred
    pass's ``to_fetch=0`` short-circuit fires (e.g. e2ec21ad 2026-08-05).

    Part of #1+#6 fix from
    docs/superpowers/plans/2026-08-05-image-chain-9-fixes.md.
    """
    if system is None:
        return results
    cumulative = getattr(system, "all_links_of_system", None)
    if not cumulative:
        return results
    merged = dict(results)
    merged["all_links_of_system"] = list(cumulative)
    return merged


def _split_cited_urls(
    cited_urls: set[str], url_to_html: dict[str, str]
) -> tuple[set[str], list[str]]:
    """Partition ``cited_urls`` into (already_covered, still_to_fetch).

    Reads the source of truth ``url_to_html`` directly so the partition
    matches what ``build_citation_index`` will use downstream. The
    invariant ``len(covered) + len(gap) == len(cited_urls)`` always
    holds here (per-URL partition, no skip).

    Fix #3 from
    docs/superpowers/plans/2026-08-05-image-chain-9-fixes.md.
    """
    covered: set[str] = set()
    gap: list[str] = []
    for url in cited_urls:
        if url_to_html.get(url):
            covered.add(url)
        else:
            gap.append(url)
    return covered, gap


def _deferred_image_fill(
    research_id: str,
    *,
    final_markdown: str,
    results: dict,
    settings_snapshot: dict,
    progress_callback=None,
) -> int:
    """One-pass image fill for the cited URLs of a finalized report.

    Replaces the previous per-LLM-round ``_ensure_images_for_results``
    fetch loop, which dominated research walltime on langgraph runs
    (the Shanghai 2026-08-03 study spent ~7 hours scraping ~80-200
    pages per LLM reasoning round for 22 rounds, while the langgraph
    agent itself only wanted the text snippets).

    New contract:

    1. Run AFTER the markdown report + ``## Sources`` block are
       finalised (i.e. after ``report_generator.generate_report`` in
       quick mode or after the detailed-mode per-subsection assembly
       in detailed mode) and BEFORE
       ``enhance_report_with_images``.
    2. Parse the report's ``## Sources`` block to extract the LLM-
       cited URL set (``num_to_url`` keys), exactly the same set
       ``build_citation_index`` will use downstream.
    3. Fetch images (Playwright first, Firecrawl fallback per URL)
       for every cited URL whose ``html_content`` slot in
       ``results["findings"][].search_results[]`` is still empty.
       URLs that already have ``html_content`` from earlier text
       fetches are left untouched.
    4. JSON-serialize the per-URL image list and write it back to
       the matching ``search_results`` record so
       ``enhance_report_with_images``'s ``build_citation_index``
       finds the populated ``url_to_html`` map and proceeds.

    Returns the count of cited URLs whose ``html_content`` was
    filled in this pass. ``0`` is normal when the LLM never
    cited a URL, when ``report.enable_images`` is off, or when
    every cited URL was already fetched for text. Errors at the
    fetch / serialization level are downgraded to debug logs so
    a partial fill does not fail the whole research.
    """
    # Only meaningful when images are enabled — the caller should
    # gate on the same setting the postprocessing gate uses.
    from ...config.thread_settings import get_setting_from_snapshot

    if not get_setting_from_snapshot(
        "report.enable_images", False, settings_snapshot=settings_snapshot
    ):
        logger.info(
            f"[IMG-TRACE] DEFERRED_FILL research={research_id} "
            f"skipped reason=enable_images=False"
        )
        return 0

    # 1. Parse the cited URL set out of the finalised report.
    try:
        from ...advanced_search_system.strategies.langgraph_agent_strategy import (
            _parse_sources_markdown_urls,
        )
    except Exception:
        _parse_sources_markdown_urls = None  # type: ignore[assignment]

    # Build the cited-URL set AND the per-URL citation-number map.
    # We always go through ``build_citation_index`` (which is cheap
    # and reuses the existing infrastructure) so the deferred pass
    # has the (cite_num, ref_url) pair per URL available to emit on
    # the DEFERRED_FETCHED_IMG / DEFERRED_FILLED log lines.
    try:
        from ...images.relevance import build_citation_index

        num_to_url, _section_to_nums, _url_to_html = build_citation_index(
            final_markdown, results
        )
        cited_urls = set(num_to_url.values())
        # Per-URL inverse: {url: citation_number_str}. One URL
        # may appear under multiple cite numbers in pathological
        # cases (e.g. LLM cites the same source twice); we keep
        # the first occurrence (insertion order in Python 3.7+).
        _url_to_cite_num: dict[str, str] = {}
        for num, url in num_to_url.items():
            if url not in _url_to_cite_num:
                _url_to_cite_num[url] = num
    except Exception:
        logger.exception(
            "Failed to build citation index for deferred image fill"
        )
        cited_urls = set()
        _url_to_cite_num = {}

    if not cited_urls:
        logger.info(
            f"[IMG-TRACE] DEFERRED_FILL research={research_id} "
            f"skipped reason=no_cited_urls"
        )
        return 0

    # 2. Compute covered/gap from the same source build_citation_index
    # used downstream. Pre-#3 the loop iterated search_results[]
    # only, which gave `already_html=2 / to_fetch=0` even when 9
    # subsections had fetched cited URLs but only the last survived
    # the per-subsection reset() (e2ec21ad 2026-08-05).
    # url_to_html is the truth — covered + gap == len(cited_urls)
    # always holds.
    url_already_has_html, urls_to_fetch = _split_cited_urls(
        cited_urls, url_to_html
    )
    # Invariant check — log a loud warning if the inputs are inconsistent
    # rather than silently producing self-contradicting counts.
    total = len(cited_urls)
    cov = len(url_already_has_html)
    gap = len(urls_to_fetch)
    if cov + gap != total:
        logger.warning(
            f"[IMG-TRACE] DEFERRED_FILL invariant_violated research={research_id} "
            f"cited={total} covered={cov} gap={gap} sum={cov+gap}"
        )
    logger.info(
        f"[IMG-TRACE] DEFERRED_FILL research={research_id} "
        f"cited={total} covered={cov} gap={gap}"
    )
    if not urls_to_fetch:
        return 0

    # 3. Fetch the remaining URLs in a single batch.
    try:
        from ...research_library.downloaders.extraction import (
            pipeline as extract_pipeline,
        )
        from ...images.serialize import dumps_images
    except Exception:
        logger.exception(
            "Deferred image fill: imports unavailable; skipping"
        )
        return 0

    if progress_callback is not None:
        try:
            progress_callback(
                f"Fetching images for {len(urls_to_fetch)} cited sources...",
                90,
                {"phase": "image_fetch_deferred"},
            )
        except Exception:
            pass

    try:
        data = extract_pipeline.fetch_content_with_images(
            urls_to_fetch,
            titles={},
            settings_snapshot=settings_snapshot,
        )
    except Exception:
        logger.exception(
            "Deferred image fill: fetch_content_with_images raised; "
            "continuing with text-only report"
        )
        return 0

    # 4. Serialise + write back to ``search_results[].html_content``.
    filled = 0
    for url, entry in (data or {}).items():
        images = (entry or {}).get("images", []) if entry else []
        if not images:
            continue
        try:
            payload = dumps_images(images)
        except Exception:
            logger.exception(
                f"Deferred image fill: dumps_images failed for {url}"
            )
            continue
        # Per-URL summary that records the cite_num + ref_url
        # association we know about (extracted from the
        # ``## Sources`` block before the fetch ran) plus the count
        # of images attached. A log consumer can union this with
        # the per-image DEFERRED_FETCHED_IMG lines below to get
        # the (alt, source_url, ref_url, cite_num) tuple per image.
        cite_num_for_url = _url_to_cite_num.get(url, "-")
        # Per-image DEFERRED_FETCHED_IMG so the deferred pass leaves
        # the same five-key trail as the other IMG-TRACE stages.
        # ``cite_num`` is unknown at fetch time, ``ref_url`` is the
        # URL itself (== the cited reference page that the agent
        # will reference). ``img_source_url`` is the page the image
        # was extracted from (= ref_url in this single-pass case).
        for img in images:
            logger.info(
                f"[IMG-TRACE] DEFERRED_FETCHED_IMG research={research_id} "
                f"img_alt={(getattr(img, 'alt', '') or '')[:200]!r} "
                f"img_url={getattr(img, 'url', '')} "
                f"img_source_url={getattr(img, 'source_url', '')} "
                f"cite_num={cite_num_for_url} "
                f"ref_url={url}"
            )
        attached = False
        for finding in results.get("findings", []) or []:
            for sr in finding.get("search_results", []) or []:
                sr_url = sr.get("url") or sr.get("link") or ""
                if sr_url != url:
                    continue
                sr["html_content"] = payload
                attached = True
        if attached:
            filled += 1
            # Summary event — carries the full four-field vocabulary
            # the user asked for (cite_num, ref_url, img_source_url,
            # img_alt) so a single grep ``DEFERRED_FILLED`` line tells
            # you the citation number, the reference URL, the page
            # the images came from, and the alt text of every image
            # that was attached. The per-image ``DEFERRED_FETCHED_IMG``
            # lines above carry the same fields one image at a time;
            # this is the at-a-glance summary.
            alts_repr = ", ".join(
                repr((getattr(img, "alt", "") or "")[:200])
                for img in images
            )
            src_url = (
                getattr(images[0], "source_url", "")
                if images
                else url
            )
            logger.info(
                f"[IMG-TRACE] DEFERRED_FILLED research={research_id} "
                f"img_alt_count={len(images)} "
                f"img_source_url={src_url} "
                f"img_alt=[{alts_repr}] "
                f"cite_num={cite_num_for_url} "
                f"ref_url={url}"
            )
    logger.info(
        f"[IMG-TRACE] DEFERRED_FILL research={research_id} done "
        f"filled={filled}/{len(urls_to_fetch)}"
    )
    return filled


@log_for_research
@thread_cleanup
def run_research_process(research_id, query, mode, **kwargs):
    """
    Run the research process in the background for a given research ID.

    Args:
        research_id: The ID of the research
        query: The research query
        mode: The research mode (quick/detailed)
        **kwargs: Additional parameters for the research (model_provider, model, search_engine, etc.)
                 MUST include 'username' for database access
    """
    from ..routes.globals import (
        is_research_active,
        is_termination_requested,
        update_progress_and_check_active,
    )

    # Extract username - required for database access
    username = kwargs.get("username")
    if not username:
        logger.error("No username provided to research thread")
        raise ValueError("Username is required for research process")
    # Extract user_password early so it's available for all cleanup paths
    user_password = kwargs.get("user_password")

    # Wall-clock anchor for the [PERF] event=summary line emitted on
    # every exit path (success / failure / suspension). Lets after-the-fact
    # analysis see the total research cost independent of per-stage sums.
    _t_overall_start = time.monotonic()

    # Establish thread context FIRST so every subsequent log line in this
    # thread can be attributed to the correct user/research and persisted
    # to the user's encrypted ResearchLog. Otherwise the early INFO logs
    # below ("Research thread started", "Research strategy", "Research
    # parameters") fire before start_research_process gets to its own
    # set_search_context call (~line 417) and the daemon can't open the
    # encrypted DB to write them — silently dropped via the bare-except.
    set_search_context(
        {
            "research_id": research_id,
            "username": username,
            "user_password": user_password,
        }
    )

    logger.info(f"Research thread started with username: {username}")

    try:
        # Check if this research has been terminated before we even start
        if is_termination_requested(research_id):
            logger.info(
                f"Research {research_id} was terminated before starting"
            )
            cleanup_research_resources(
                research_id,
                username,
                user_password=user_password,
                final_status=ResearchStatus.SUSPENDED,
            )
            return

        logger.info(
            f"Starting research process for ID {research_id}, query: {query}"
        )

        # Extract key parameters
        model_provider = kwargs.get("model_provider")
        model = kwargs.get("model")
        custom_endpoint = kwargs.get("custom_endpoint")
        search_engine = kwargs.get("search_engine")
        max_results = kwargs.get("max_results")
        time_period = kwargs.get("time_period")
        iterations = kwargs.get("iterations")
        questions_per_iteration = kwargs.get("questions_per_iteration")
        strategy = kwargs.get(
            "strategy", "source-based"
        )  # Default to source-based
        settings_snapshot = kwargs.get(
            "settings_snapshot", {}
        )  # Complete settings snapshot

        # The per-user settings DB does not always carry the values that
        # the container's compose file injects as ``LDR_*`` env vars (e.g.
        # the SearXNG instance URL). The standalone diagnostic CLI
        # (``scripts/check_engines.py``) already bridges this by reading
        # env vars into a snapshot; mirror that here so the in-research
        # pre-flight has the same view of the world as the CLI.
        settings_snapshot = _merge_env_into_snapshot(settings_snapshot)

        # Log settings snapshot to debug
        from ...settings.logger import log_settings

        log_settings(settings_snapshot, "Settings snapshot received in thread")

        # Strategy should already be saved in the database before thread starts
        logger.info(f"Research strategy: {strategy}")

        # Log all parameters for debugging
        logger.info(
            f"Research parameters: provider={model_provider}, model={model}, "
            f"search_engine={search_engine}, max_results={max_results}, "
            f"time_period={time_period}, iterations={iterations}, "
            f"questions_per_iteration={questions_per_iteration}, "
            f"custom_endpoint={custom_endpoint}, strategy={strategy}"
        )

        # Set up the AI Context Manager
        output_dir = OUTPUT_DIR / f"research_{research_id}"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create a settings context that uses snapshot - no database access in threads
        settings_context = SnapshotSettingsContext(
            settings_snapshot, username=username
        )

        # Only log settings if explicitly enabled via LDR_LOG_SETTINGS env var
        from ...settings.logger import log_settings

        log_settings(
            settings_context.values, "SettingsContext values extracted"
        )

        # Set the settings context for this thread
        from ...config.thread_settings import (
            set_settings_context,
        )

        set_settings_context(settings_context)

        # user_password already extracted above (before termination check)

        # Create shared research context that can be updated during research
        shared_research_context = {
            "research_id": research_id,
            "research_query": query,
            "research_mode": mode,
            "research_phase": "init",
            "search_iteration": 0,
            "search_engines_planned": None,
            "search_engine_selected": search_engine,
            "username": username,  # Add username for queue operations
            "user_password": user_password,  # Add password for metrics access
        }

        # If this is a follow-up research, include the parent context
        if "research_context" in kwargs and kwargs["research_context"]:
            logger.info(
                f"Adding parent research context with {len(kwargs['research_context'].get('past_findings', ''))} chars of findings"
            )
            shared_research_context.update(kwargs["research_context"])

        # Do not log context keys as they may contain sensitive information
        logger.info(f"Created shared_research_context for user: {username}")

        # Set search context for search tracking
        set_search_context(shared_research_context)

        # Set up progress callback
        def progress_callback(message, progress_percent, metadata):
            # Frequent termination check.
            #
            # Skip when we are already in the error-handling path
            # (phase == "error"): a real error reached the outer except
            # handler, so the research has genuinely failed — we must
            # not overwrite that status with SUSPENDED just because the
            # user also happened to click cancel while the request was
            # in flight. The clean-cancel case is handled by the
            # explicit `except ResearchTerminatedException` block.
            if metadata.get("phase") != "error" and is_termination_requested(research_id):
                handle_termination(research_id, username)
                raise ResearchTerminatedException(  # noqa: TRY301 — inside nested callback, not caught by enclosing try
                    "Research was terminated by user"
                )

            # Silent phase — no UI logging or socket emission needed
            if metadata.get("phase") == "termination_check":
                return

            # Bind research_id AND username so the database_sink + queue
            # daemon can resolve the per-user encrypted DB. Without username
            # the daemon's _write_log_to_database hits "No authenticated
            # user", silently swallows the error, and ResearchLog ends up
            # with zero milestone rows — leaving /api/research/<id>/status
            # without a log_entry to render and the frontend stuck on the
            # "Performing research..." fallback.
            bound_logger = logger.bind(
                research_id=research_id, username=username
            )
            bound_logger.log("MILESTONE", message)

            if "SEARCH_PLAN:" in message:
                engines = message.split("SEARCH_PLAN:")[1].strip()
                metadata["planned_engines"] = engines
                metadata["phase"] = "search_planning"  # Use existing phase
                # Update shared context for token tracking
                shared_research_context["search_engines_planned"] = engines
                shared_research_context["research_phase"] = "search_planning"

            if "ENGINE_SELECTED:" in message:
                engine = message.split("ENGINE_SELECTED:")[1].strip()
                metadata["selected_engine"] = engine
                metadata["phase"] = "search"  # Use existing 'search' phase
                # Update shared context for token tracking
                shared_research_context["search_engine_selected"] = engine
                shared_research_context["research_phase"] = "search"

            # Capture other research phases for better context tracking
            if metadata.get("phase"):
                shared_research_context["research_phase"] = metadata["phase"]

            # Update search iteration if available
            if "iteration" in metadata:
                shared_research_context["search_iteration"] = metadata[
                    "iteration"
                ]

            # Adjust progress based on research mode
            adjusted_progress = progress_percent
            if (
                mode == "detailed"
                and metadata.get("phase") == "output_generation"
            ):
                # For detailed mode, adjust the progress range for output generation
                adjusted_progress = min(80, progress_percent)
            elif (
                mode == "detailed"
                and metadata.get("phase") == "report_generation"
            ):
                # Scale the progress from 80% to 95% for the report generation phase
                if progress_percent is not None:
                    normalized = progress_percent / 100
                    adjusted_progress = 80 + (normalized * 15)
            elif (
                mode == "quick" and metadata.get("phase") == "output_generation"
            ):
                # For quick mode, ensure we're at least at 85% during output generation
                adjusted_progress = max(85, progress_percent)
                # Map any further progress within output_generation to 85-95% range
                if progress_percent is not None and progress_percent > 0:
                    normalized = progress_percent / 100
                    adjusted_progress = 85 + (normalized * 10)

            # Atomically update progress and check if research is still active
            if adjusted_progress is not None:
                adjusted_progress, still_active = (
                    update_progress_and_check_active(
                        research_id, adjusted_progress
                    )
                )
            else:
                still_active = is_research_active(research_id)

            if still_active:
                # Queue the progress update to be processed in main thread
                if adjusted_progress is not None:
                    from ..queue.processor_v2 import queue_processor

                    if username:
                        queue_processor.queue_progress_update(
                            username, research_id, adjusted_progress
                        )
                    else:
                        logger.warning(
                            f"Cannot queue progress update for research {research_id} - no username available"
                        )

                # Emit a socket event (throttled to avoid event storms)
                try:
                    # Always emit completion/error states immediately;
                    # throttle intermediate progress updates
                    phase = metadata.get("phase", "")
                    is_final = (
                        phase
                        in (
                            "complete",
                            "error",
                            "report_complete",
                        )
                        or adjusted_progress == 100
                    )

                    should_emit = is_final
                    if not is_final:
                        now = time.monotonic()
                        with _last_emit_lock:
                            last = _last_emit_times.get(research_id, 0)
                            if now - last >= _EMIT_THROTTLE_SECONDS:
                                _last_emit_times[research_id] = now
                                should_emit = True
                            # Periodic TTL cleanup for orphaned entries
                            global _emit_cleanup_counter  # noqa: PLW0603
                            _emit_cleanup_counter += 1
                            if _emit_cleanup_counter % 100 == 0:
                                stale = [
                                    rid
                                    for rid, t in _last_emit_times.items()
                                    if now - t > _EMIT_TTL_SECONDS
                                ]
                                for rid in stale:
                                    del _last_emit_times[rid]

                    if should_emit:
                        # Basic event data - include message for display
                        event_data = {
                            "progress": adjusted_progress,
                            "message": message,
                            "phase": phase,
                        }

                        # Include additional metadata for MCP/ReAct strategy display
                        if metadata.get("thought"):
                            event_data["thought"] = metadata["thought"]
                        if metadata.get("tool"):
                            event_data["tool"] = metadata["tool"]
                        if metadata.get("arguments"):
                            event_data["arguments"] = metadata["arguments"]
                        if metadata.get("iteration"):
                            event_data["iteration"] = metadata["iteration"]
                        if metadata.get("error"):
                            event_data["error"] = metadata["error"]
                        if metadata.get("content"):
                            event_data["content"] = metadata["content"]

                        SocketIOService().emit_to_subscribers(
                            "progress", research_id, event_data
                        )
                except Exception:
                    logger.exception("Socket emit error (non-critical)")

        # Function to check termination during long-running operations
        def check_termination():
            if is_termination_requested(research_id):
                handle_termination(research_id, username)
                raise ResearchTerminatedException(  # noqa: TRY301 — inside nested callback, not caught by enclosing try
                    "Research was terminated by user during long-running operation"
                )
            return False  # Not terminated

        # Configure the system with the specified parameters
        use_llm = None
        if model or search_engine or model_provider:
            # Log that we're overriding system settings
            logger.info(
                f"Overriding system settings with: provider={model_provider}, model={model}, search_engine={search_engine}"
            )

        # Override LLM if model or model_provider specified
        if model or model_provider:
            try:
                # Get LLM with the overridden settings
                # Use the shared_research_context which includes username
                use_llm = get_llm(
                    model_name=model,
                    provider=model_provider,
                    openai_endpoint_url=custom_endpoint,
                    research_id=research_id,
                    research_context=shared_research_context,
                )

                logger.info(
                    f"Successfully set LLM to: provider={model_provider}, model={model}"
                )
            except Exception as e:
                logger.exception(
                    f"Error setting LLM provider={model_provider}, model={model}"
                )
                error_msg = str(e)
                # Surface configuration errors to user instead of silently continuing
                config_error_keywords = [
                    "model path",
                    "llamacpp",
                    "cannot connect",
                    "server",
                    "not configured",
                    "not responding",
                    "directory",
                    ".gguf",
                ]
                if any(
                    keyword in error_msg.lower()
                    for keyword in config_error_keywords
                ):
                    # This is a configuration error the user can fix
                    raise ValueError(
                        f"LLM Configuration Error: {error_msg}"
                    ) from e
                # For other errors, re-raise to avoid silent failures
                raise

        # Create search engine first if specified, to avoid default creation without username
        use_search = None
        if search_engine:
            try:
                # Create a new search object with these settings
                use_search = get_search(
                    search_tool=search_engine,
                    llm_instance=use_llm,
                    username=username,
                    settings_snapshot=settings_snapshot,
                )
                logger.info(
                    f"Successfully created search engine: {search_engine}"
                )
            except Exception as e:
                logger.exception(
                    f"Error creating search engine {search_engine}"
                )
                error_msg = str(e)
                # Surface configuration errors to user instead of silently continuing
                config_error_keywords = [
                    "searxng",
                    "instance_url",
                    "api_key",
                    "cannot connect",
                    "connection",
                    "timeout",
                    "not configured",
                ]
                if any(
                    keyword in error_msg.lower()
                    for keyword in config_error_keywords
                ):
                    # This is a configuration error the user can fix
                    raise ValueError(
                        f"Search Engine Configuration Error ({search_engine}): {error_msg}"
                    ) from e
                # For other errors, re-raise to avoid silent failures
                raise

        # === Pre-flight: search engine + service health check ===
        # Probes each SearXNG backend (and Firecrawl if enabled) so the user can
        # see which engines currently return results before iterations begin.
        # Failures are advisory only — never block the research.
        try:
            progress_callback(
                "预检: 正在测试搜索引擎健康状态...",
                3,
                {"phase": "preflight", "step": "start"},
            )
            from ...diagnostics.engine_health import (
                format_status_table,
                run_preflight_check,
            )

            statuses = run_preflight_check(settings_snapshot=settings_snapshot)
            table = format_status_table(statuses)
            ok_count = sum(1 for s in statuses if s.status == "ok")
            active_count = sum(1 for s in statuses if s.status != "skipped")
            if ok_count > 0:
                progress_callback(
                    f"预检完成: {ok_count}/{active_count} 个引擎/服务可用\n{table}",
                    4,
                    {
                        "phase": "preflight",
                        "step": "done",
                        "status": "ok",
                        "ok": ok_count,
                        "total": active_count,
                    },
                )
            else:
                progress_callback(
                    f"⚠ 预检警告: 无可用搜索引擎, 研究将仅凭模型知识生成\n{table}",
                    4,
                    {
                        "phase": "preflight",
                        "step": "done",
                        "status": "warning",
                        "ok": 0,
                        "total": active_count,
                    },
                )
        except Exception as preflight_err:  # noqa: BLE001
            # A broken probe must never abort the research. run_preflight_check
            # itself never raises (each probe captures its own error), so this
            # only fires on unexpected setup errors (e.g. import failure). Per
            # requirement, still emit a FULL report — never a one-line skip.
            logger.exception("Pre-flight engine health check errored")
            error_report = (
                "引擎健康预检 (探测框架异常, 无法执行逐项检测):\n"
                f"  ✗ proxy      error  预检未执行: {preflight_err}\n"
                f"  ✗ searxng    error  预检未执行: {preflight_err}\n"
                f"  ✗ firecrawl  error  预检未执行: {preflight_err}\n"
                "可用引擎/服务: 0/3"
            )
            progress_callback(
                f"⚠ 预检异常: 探测框架未能运行, 请检查诊断模块\n{error_report}",
                4,
                {
                    "phase": "preflight",
                    "step": "done",
                    "status": "warning",
                    "ok": 0,
                    "total": 3,
                    "error": str(preflight_err),
                },
            )

        # Set the progress callback in the system
        system = AdvancedSearchSystem(
            llm=use_llm,  # type: ignore[arg-type]
            search=use_search,  # type: ignore[arg-type]
            strategy_name=strategy,
            max_iterations=iterations,
            questions_per_iteration=questions_per_iteration,
            username=username,
            settings_snapshot=settings_snapshot,
            research_id=research_id,
            research_context=shared_research_context,
        )
        system.set_progress_callback(progress_callback)

        # Run the search
        progress_callback("Starting research process", 5, {"phase": "init"})

        try:
            with _perf_stage(research_id, "analyze_topic"):
                results = system.analyze_topic(query)
            if mode == "quick":
                progress_callback(
                    "Search complete, preparing to generate summary...",
                    85,
                    {"phase": "output_generation"},
                )
            else:
                progress_callback(
                    "Search complete, generating output",
                    80,
                    {"phase": "output_generation"},
                )
        except Exception as search_error:
            # Better handling of specific search errors
            error_message = str(search_error)
            error_type = "unknown"

            # OpenAI-compatible runtime failures (LM Studio / vLLM / llama.cpp
            # server / OpenRouter / custom endpoint) -- rewrite to a message
            # that names the provider, base URL, and model (#3878).
            if model_provider in {
                "openai_endpoint",
                "lmstudio",
                "llamacpp",
                "openai",
                "openrouter",
                "google",
                "ionos",
                "xai",
            } and is_openai_compat_runtime_error(search_error):
                rewritten = friendly_openai_compatible_error(
                    search_error,
                    provider=model_provider,
                    base_url=custom_endpoint,
                    model=model,
                )
                raise RuntimeError(rewritten) from search_error

            # Extract error details for common issues
            if "status code: 503" in error_message:
                error_message = "Ollama AI service is unavailable (HTTP 503). Please check that Ollama is running properly on your system."
                error_type = "ollama_unavailable"
            elif "status code: 404" in error_message:
                error_message = "Ollama model not found (HTTP 404). Please check that you have pulled the required model."
                error_type = "model_not_found"
            elif "status code:" in error_message:
                # Extract the status code for other HTTP errors
                status_code = error_message.split("status code:")[1].strip()
                error_message = f"API request failed with status code {status_code}. Please check your configuration."
                error_type = "api_error"
            elif "connection" in error_message.lower():
                error_message = "Connection error. Please check that your LLM service (Ollama/API) is running and accessible."
                error_type = "connection_error"

            # Raise with improved error message
            raise RuntimeError(
                f"{error_message} (Error type: {error_type})"
            ) from search_error

        # Generate output based on mode
        if mode == "quick":
            # Quick Summary
            if results.get("findings") or results.get("formatted_findings"):
                raw_formatted_findings = results["formatted_findings"]

                # Track whether synthesis hit an error and we fell back to
                # raw findings. When True we still produce a (partial)
                # report from whatever findings were already collected,
                # but the final status will be PARTIAL_SUCCESS rather
                # than COMPLETED so the UI can distinguish "fully
                # synthesised report" from "report assembled despite a
                # synthesis-time error".
                synthesis_had_error = False

                # Check if formatted_findings contains an error message
                if isinstance(
                    raw_formatted_findings, str
                ) and raw_formatted_findings.startswith("Error:"):
                    synthesis_had_error = True
                    logger.exception(
                        f"Detected error in formatted findings: {raw_formatted_findings[:100]}..."
                    )

                    # Determine error type for better user feedback
                    error_type = "unknown"
                    error_message = raw_formatted_findings.lower()

                    if (
                        "token limit" in error_message
                        or "context length" in error_message
                    ):
                        error_type = "token_limit"
                        # Log specific error type
                        logger.warning(
                            "Detected token limit error in synthesis"
                        )

                        # Update progress with specific error type
                        progress_callback(
                            "Synthesis hit token limits. Attempting fallback...",
                            87,
                            {
                                "phase": "synthesis_error",
                                "error_type": error_type,
                            },
                        )
                    elif (
                        "timeout" in error_message
                        or "timed out" in error_message
                    ):
                        error_type = "timeout"
                        logger.warning("Detected timeout error in synthesis")
                        progress_callback(
                            "Synthesis timed out. Attempting fallback...",
                            87,
                            {
                                "phase": "synthesis_error",
                                "error_type": error_type,
                            },
                        )
                    elif "rate limit" in error_message:
                        error_type = "rate_limit"
                        logger.warning("Detected rate limit error in synthesis")
                        progress_callback(
                            "LLM rate limit reached. Attempting fallback...",
                            87,
                            {
                                "phase": "synthesis_error",
                                "error_type": error_type,
                            },
                        )
                    elif (
                        "connection" in error_message
                        or "network" in error_message
                    ):
                        error_type = "connection"
                        logger.warning("Detected connection error in synthesis")
                        progress_callback(
                            "Connection issue with LLM. Attempting fallback...",
                            87,
                            {
                                "phase": "synthesis_error",
                                "error_type": error_type,
                            },
                        )
                    elif (
                        "llm error" in error_message
                        or "final answer synthesis fail" in error_message
                    ):
                        error_type = "llm_error"
                        logger.warning(
                            "Detected general LLM error in synthesis"
                        )
                        progress_callback(
                            "LLM error during synthesis. Attempting fallback...",
                            87,
                            {
                                "phase": "synthesis_error",
                                "error_type": error_type,
                            },
                        )
                    else:
                        # Generic error
                        logger.warning("Detected unknown error in synthesis")
                        progress_callback(
                            "Error during synthesis. Attempting fallback...",
                            87,
                            {
                                "phase": "synthesis_error",
                                "error_type": "unknown",
                            },
                        )

                    # Extract synthesized content from findings if available
                    synthesized_content = ""
                    for finding in results.get("findings", []):
                        if finding.get("phase") == "Final synthesis":
                            synthesized_content = finding.get("content", "")
                            break

                    # Use synthesized content as fallback
                    if (
                        synthesized_content
                        and not synthesized_content.startswith("Error:")
                    ):
                        logger.info(
                            "Using existing synthesized content as fallback"
                        )
                        raw_formatted_findings = synthesized_content

                    # Or use current_knowledge as another fallback
                    elif results.get("current_knowledge"):
                        logger.info("Using current_knowledge as fallback")
                        raw_formatted_findings = results["current_knowledge"]

                    # Or combine all finding contents as last resort
                    elif results.get("findings"):
                        logger.info("Combining all findings as fallback")
                        # First try to use any findings that are not errors
                        valid_findings = [
                            f"## {finding.get('phase', 'Finding')}\n\n{finding.get('content', '')}"
                            for finding in results.get("findings", [])
                            if finding.get("content")
                            and not finding.get("content", "").startswith(
                                "Error:"
                            )
                        ]

                        if valid_findings:
                            raw_formatted_findings = (
                                "# Research Results (Fallback Mode)\n\n"
                            )
                            raw_formatted_findings += "\n\n".join(
                                valid_findings
                            )
                            raw_formatted_findings += f"\n\n## Error Information\n{raw_formatted_findings}"
                        else:
                            # Last resort: use everything including errors
                            raw_formatted_findings = (
                                "# Research Results (Emergency Fallback)\n\n"
                            )
                            raw_formatted_findings += "The system encountered errors during final synthesis.\n\n"
                            raw_formatted_findings += "\n\n".join(
                                f"## {finding.get('phase', 'Finding')}\n\n{finding.get('content', '')}"
                                for finding in results.get("findings", [])
                                if finding.get("content")
                            )

                    progress_callback(
                        f"Using fallback synthesis due to {error_type} error",
                        88,
                        {
                            "phase": "synthesis_fallback",
                            "error_type": error_type,
                        },
                    )

                # If after all fallback attempts the formatted findings
                # still look like a raw error message, we have nothing
                # useful to show: the final status should be FAILED, not
                # PARTIAL_SUCCESS.
                fallback_failed = (
                    synthesis_had_error
                    and isinstance(raw_formatted_findings, str)
                    and raw_formatted_findings.startswith("Error:")
                )

                logger.info(
                    "Found formatted_findings of length: {}",
                    len(str(raw_formatted_findings)),
                )

                try:
                    # Check if we have an error in the findings and use enhanced error handling
                    if isinstance(
                        raw_formatted_findings, str
                    ) and raw_formatted_findings.startswith("Error:"):
                        logger.info(
                            "Generating enhanced error report using ErrorReportGenerator"
                        )

                        # Generate comprehensive error report
                        # ErrorReportGenerator does not use LLM (kept for compat)
                        error_generator = ErrorReportGenerator()
                        clean_markdown = error_generator.generate_error_report(
                            error_message=raw_formatted_findings,
                            query=query,
                            partial_results=results,
                            search_iterations=results.get("iterations", 0),
                            research_id=research_id,
                        )

                        logger.info(
                            "Generated enhanced error report with {} characters",
                            len(clean_markdown),
                        )
                    else:
                        # Get the synthesized content from the LLM directly
                        clean_markdown = raw_formatted_findings

                    # Extract all sources from findings to add them to the summary
                    all_links = []
                    for finding in results.get("findings", []):
                        search_results = finding.get("search_results", [])
                        if search_results:
                            try:
                                links = extract_links_from_search_results(
                                    search_results
                                )
                                all_links.extend(links)
                            except Exception:
                                logger.exception(
                                    "Error processing search results/links"
                                )

                    logger.info(
                        "Successfully converted to clean markdown of length: {}",
                        len(clean_markdown),
                    )

                    # === Image post-processing (gated by report.enable_images) ===
                    try:
                        from ...images.postprocessing import (
                            enhance_report_with_images,
                        )
                        with _open_image_enhancer_session(
                            username, settings_snapshot
                        ) as (img_args, img_db_session):
                            if not img_args["enable_images"]:
                                logger.info(
                                    f"[IMG-TRACE] SKIP research={research_id} "
                                    f"reason=enable_images=False"
                                )
                            else:
                                # Deferred image fill: scrape the cited
                                # source pages once, AFTER the report
                                # is finalised, and attach the
                                # extracted images to
                                # ``search_results[].html_content``
                                # so the postprocessing stage finds
                                # them via ``build_citation_index``.
                                # This replaces the per-LLM-round
                                # image-fill that previously ran
                                # inside the langgraph agent loop
                                # and dominated research walltime
                                # (~7 h on the 2026-08-03 Shanghai
                                # run).
                                #
                                # Inject the cross-subsection
                                # cumulative all_links_of_system
                                # so build_citation_index sees
                                # fetch results from every
                                # subsection, not just the last
                                # one (fix #1+#6).
                                results_for_fill = _inject_all_links_of_system(
                                    results, system
                                )
                                _deferred_image_fill(
                                    research_id,
                                    final_markdown=clean_markdown,
                                    results=results_for_fill,
                                    settings_snapshot=settings_snapshot,
                                    progress_callback=progress_callback,
                                )
                                progress_callback(
                                    "Enhancing report with real images...",
                                    92,
                                    {"phase": "image_enhancement"},
                                )
                                with _perf_stage(
                                    research_id, f"image_enhancement:quick"
                                ):
                                    clean_markdown = enhance_report_with_images(
                                        research_id=research_id,
                                        clean_markdown=clean_markdown,
                                        results=results,
                                        db_session=img_db_session,
                                        **img_args,
                                    )
                    except Exception:
                        logger.exception(
                            "Image enhancement step failed; continuing with text-only report"
                        )

                    # First send a progress update for generating the summary
                    progress_callback(
                        "Generating clean summary from research data...",
                        90,
                        {"phase": "output_generation"},
                    )

                    # Send progress update for saving report
                    progress_callback(
                        "Saving research report to database...",
                        95,
                        {"phase": "report_complete"},
                    )

                    # Enforce ascending ## Sources [N] and drop orphan
                    # body citations. See the detailed-mode site for the
                    # rationale (image enhancement is upstream of this
                    # step so its citation matching is unaffected).
                    try:
                        from ..text_optimization.citation_formatter import (
                            enforce_sources_ascending_and_drop_orphans,
                        )

                        with _perf_stage(research_id, "sources_enforce:quick"):
                            clean_markdown = (
                                enforce_sources_ascending_and_drop_orphans(
                                    clean_markdown
                                )
                            )
                    except Exception:
                        logger.exception(
                            "Quick-mode sources-enforce step failed; "
                            "continuing with unenforced content"
                        )

                    # Format citations in the markdown content
                    formatter = get_citation_formatter()
                    with _perf_stage(research_id, "citation_format:quick"):
                        formatted_content = formatter.format_document(
                            clean_markdown
                        )

                    # Prepare complete report content
                    full_report_content = (
                        f"{formatted_content}\n\n"
                        + _("## Research Metrics") + "\n"
                        + _("- Search Iterations: {n}").format(n=results["iterations"]) + "\n"
                        + _("- Generated at: {ts}").format(ts=datetime.now(UTC).isoformat()) + "\n"
                    )

                    # Save sources to database (non-fatal - report should still
                    # be saved even if source saving fails)
                    try:
                        from .research_sources_service import (
                            ResearchSourcesService,
                        )

                        sources_service = ResearchSourcesService()
                        if all_links:
                            logger.info(
                                f"Quick summary: Saving {len(all_links)} sources to database"
                            )
                            with _perf_stage(research_id, "save_sources:quick"):
                                sources_saved = (
                                    sources_service.save_research_sources(
                                        research_id=research_id,
                                        sources=all_links,
                                        username=username,
                                    )
                            )
                            logger.info(
                                f"Quick summary: Saved {sources_saved} sources for research {research_id}"
                            )
                    except Exception:
                        logger.exception(
                            f"Failed to save sources for research {research_id} (continuing with report save)"
                        )

                    # Save report using storage abstraction
                    from ...storage import get_report_storage

                    with get_user_db_session(username) as db_session:
                        storage = get_report_storage(session=db_session)

                        # Prepare metadata
                        metadata = {
                            "iterations": results["iterations"],
                            "generated_at": datetime.now(UTC).isoformat(),
                        }

                        # Save report using storage abstraction
                        success = storage.save_report(
                            research_id=research_id,
                            content=full_report_content,
                            metadata=metadata,
                            username=username,
                        )

                        if not success:
                            raise RuntimeError("Failed to save research report")  # noqa: TRY301 — triggers research failure handling in outer except

                        logger.info(
                            f"Report saved for research_id: {research_id}"
                        )

                    # Skip export to additional formats - we're storing in database only

                    # Update research status in database
                    completed_at = datetime.now(UTC).isoformat()

                    with get_user_db_session(username) as db_session:
                        research = (
                            db_session.query(ResearchHistory)
                            .filter_by(id=research_id)
                            .first()
                        )

                        # Preserve existing metadata and update with new values
                        metadata = _parse_research_metadata(
                            research.research_meta
                        )

                        metadata.update(
                            {
                                "iterations": results["iterations"],
                                "generated_at": datetime.now(UTC).isoformat(),
                            }
                        )

                        # Use the helper function for consistent duration calculation
                        duration_seconds = calculate_duration(
                            research.created_at, completed_at
                        )

                        # Three-way status: FAILED if fallback also gave
                        # us no useful content; PARTIAL_SUCCESS if
                        # synthesis errored but fallback recovered some
                        # findings; COMPLETED otherwise.
                        if fallback_failed:
                            research.status = ResearchStatus.FAILED
                        elif synthesis_had_error:
                            research.status = ResearchStatus.PARTIAL_SUCCESS
                        else:
                            research.status = ResearchStatus.COMPLETED
                        research.completed_at = completed_at
                        research.duration_seconds = duration_seconds
                        # Note: report_content is saved by CachedResearchService
                        # report_path is not used in encrypted database version

                        # Generate headline and topics only for news searches
                        if (
                            metadata.get("is_news_search")
                            or metadata.get("search_type") == "news_analysis"
                        ):
                            try:
                                from ...news.utils.headline_generator import (
                                    generate_headline,
                                )
                                from ...news.utils.topic_generator import (
                                    generate_topics,
                                )

                                # Get the report content from database for better headline/topic generation
                                report_content = ""
                                try:
                                    research = (
                                        db_session.query(ResearchHistory)
                                        .filter_by(id=research_id)
                                        .first()
                                    )
                                    if research and research.report_content:
                                        report_content = research.report_content
                                        logger.info(
                                            f"Retrieved {len(report_content)} chars from database for headline generation"
                                        )
                                    else:
                                        logger.warning(
                                            f"No report content found in database for research_id: {research_id}"
                                        )
                                except Exception:
                                    logger.warning(
                                        "Could not retrieve report content from database"
                                    )

                                # Generate headline
                                logger.info(
                                    f"Generating headline for query: {query[:100]}"
                                )
                                headline = generate_headline(
                                    query, report_content
                                )
                                metadata["generated_headline"] = headline

                                # Generate topics
                                logger.info(
                                    f"Generating topics with category: {metadata.get('category', 'News')}"
                                )
                                topics = generate_topics(
                                    query=query,
                                    findings=report_content,
                                    category=metadata.get("category", "News"),
                                    max_topics=6,
                                )
                                metadata["generated_topics"] = topics

                                logger.info(f"Generated headline: {headline}")
                                logger.info(f"Generated topics: {topics}")

                            except Exception:
                                logger.warning(
                                    "Could not generate headline/topics"
                                )

                        research.research_meta = metadata

                        db_session.commit()
                        logger.info(
                            f"Database commit completed for research_id: {research_id}"
                        )

                        # Update subscription if this was triggered by a subscription
                        if metadata.get("subscription_id"):
                            try:
                                from ...news.subscription_manager.storage import (
                                    SQLSubscriptionStorage,
                                )
                                from datetime import (
                                    datetime as dt,
                                    timezone,
                                    timedelta,
                                )

                                sub_storage = SQLSubscriptionStorage(db_session)
                                subscription_id = metadata["subscription_id"]

                                # Get subscription to find refresh interval
                                subscription = sub_storage.get(subscription_id)
                                if subscription:
                                    refresh_minutes = subscription.get(
                                        "refresh_minutes", 240
                                    )
                                    now = dt.now(timezone.utc)
                                    next_refresh = now + timedelta(
                                        minutes=refresh_minutes
                                    )

                                    # Update refresh times
                                    sub_storage.update_refresh_time(
                                        subscription_id=subscription_id,
                                        last_refresh=now,
                                        next_refresh=next_refresh,
                                    )

                                    # Increment stats
                                    sub_storage.increment_stats(
                                        subscription_id, 1
                                    )

                                    logger.info(
                                        f"Updated subscription {subscription_id} refresh times"
                                    )
                            except Exception:
                                logger.warning(
                                    "Could not update subscription refresh time"
                                )

                    logger.info(
                        f"Database updated successfully for research_id: {research_id}"
                    )

                    # Send the final completion message
                    progress_callback(
                        "Research completed successfully",
                        100,
                        {"phase": "complete"},
                    )

                    # Clean up resources
                    logger.info(
                        "Cleaning up resources for research_id: {}", research_id
                    )
                    cleanup_research_resources(
                        research_id,
                        username,
                        user_password=user_password,
                        final_status=(
                            ResearchStatus.FAILED
                            if fallback_failed
                            else (
                                ResearchStatus.PARTIAL_SUCCESS
                                if synthesis_had_error
                                else ResearchStatus.COMPLETED
                            )
                        ),
                    )
                    logger.info(
                        "Resources cleaned up for research_id: {}", research_id
                    )

                except Exception as inner_e:
                    logger.exception("Error during quick summary generation")
                    raise RuntimeError(
                        f"Error generating quick summary: {inner_e!s}"
                    )
            else:
                raise RuntimeError(  # noqa: TRY301 — triggers research failure handling in outer except
                    "No research findings were generated. Please try again."
                )
        else:
            # Full Report
            progress_callback(
                "Generating detailed report...",
                85,
                {"phase": "report_generation"},
            )

            # Extract the search system from the results if available
            search_system = results.get("search_system", None)

            # Wrapper that maps report generator's 0-100% to 85-95% range
            # and relays cancellation checks through the outer progress_callback
            def report_progress_callback(message, progress_percent, metadata):
                if progress_percent is not None:
                    adjusted = 85 + (progress_percent / 100) * 10
                else:
                    adjusted = progress_percent
                progress_callback(message, adjusted, metadata)

            # Pass the existing search system to maintain citation indices
            report_generator = IntegratedReportGenerator(
                search_system=search_system,
                settings_snapshot=settings_snapshot,
            )
            final_report = report_generator.generate_report(
                results, query, progress_callback=report_progress_callback
            )

            # === Detailed-mode image enhancement (parity with quick branch) ===
            try:
                # The previous per-LLM-round image-fill loop was
                # removed (2026-08-04). The image fetch is now a
                # single post-finalise pass — see
                # ``_deferred_image_fill`` for the new contract.
                logger.info(
                    f"[IMG-TRACE] DETAILED_MODE_BEGIN research={research_id} "
                    f"markdown_len={len(final_report['content'])}"
                )
                from ...images.postprocessing import (
                    enhance_report_with_images,
                )
                with _open_image_enhancer_session(
                    username, settings_snapshot
                ) as (img_args, img_db_session):
                    if not img_args["enable_images"]:
                        logger.info(
                            f"[IMG-TRACE] SKIP research={research_id} "
                            f"reason=enable_images=False"
                        )
                    else:
                        # One-pass image fill for the cited sources
                        # of the now-finalised detailed report.
                        # Replaces the per-round langgraph auto-
                        # fill that previously added ~7 h of
                        # Playwright rendering to a 7-round
                        # Shanghai run.
                        #
                        # Inject the cross-subsection cumulative
                        # all_links_of_system so the deferred pass
                        # sees fetch results from every subsection
                        # (fix #1+#6).
                        results_for_fill = _inject_all_links_of_system(
                            results, search_system
                        )
                        _deferred_image_fill(
                            research_id,
                            final_markdown=final_report["content"],
                            results=results_for_fill,
                            settings_snapshot=settings_snapshot,
                            progress_callback=progress_callback,
                        )
                        progress_callback(
                            "Enhancing detailed report with real images...",
                            92,
                            {"phase": "image_enhancement"},
                        )
                        with _perf_stage(
                            research_id, f"image_enhancement:detailed"
                        ):
                            final_report["content"] = (
                                enhance_report_with_images(
                                    research_id=research_id,
                                    clean_markdown=final_report["content"],
                                    results=results,
                                    db_session=img_db_session,
                                    **img_args,
                                )
                            )
            except Exception:
                logger.exception(
                    "Detailed-mode image enhancement step failed; "
                    "continuing with text-only report"
                )

            progress_callback(
                "Report generation complete", 95, {"phase": "report_complete"}
            )

            # Enforce ascending ## Sources [N] and drop orphan body
            # citations. Runs BEFORE the citation formatter so the
            # formatter only sees the cleaned body + ascending block;
            # the formatter then hyperlinks each remaining [[N]].
            # Image enhancement runs upstream and relies on the original
            # [N] numbers for citation matching — by placing the
            # enforcer after it, we preserve that match while
            # guaranteeing ascending order in the user-visible report.
            try:
                from ..text_optimization.citation_formatter import (
                    enforce_sources_ascending_and_drop_orphans,
                )

                with _perf_stage(research_id, "sources_enforce:detailed"):
                    final_report["content"] = (
                        enforce_sources_ascending_and_drop_orphans(
                            final_report["content"]
                        )
                    )
            except Exception:
                logger.exception(
                    "Detailed-mode sources-enforce step failed; "
                    "continuing with unenforced content"
                )

            # Format citations in the report content
            formatter = get_citation_formatter()
            with _perf_stage(research_id, "citation_format:detailed"):
                formatted_content = formatter.format_document(
                    final_report["content"]
                )

            # Save sources to database (non-fatal - report should still be saved
            # even if source saving fails, e.g. due to expired session password)
            try:
                from .research_sources_service import ResearchSourcesService

                sources_service = ResearchSourcesService()
                all_links = getattr(search_system, "all_links_of_system", None)
                if all_links:
                    logger.info(f"Saving {len(all_links)} sources to database")
                    with _perf_stage(research_id, "save_sources:detailed"):
                        sources_saved = sources_service.save_research_sources(
                            research_id=research_id,
                            sources=all_links,
                            username=username,
                        )
                    logger.info(
                        f"Saved {sources_saved} sources for research {research_id}"
                    )
            except Exception:
                logger.exception(
                    f"Failed to save sources for research {research_id} (continuing with report save)"
                )

            # Save report to database
            with get_user_db_session(username) as db_session:
                # Update metadata
                metadata = final_report["metadata"]
                metadata["iterations"] = results["iterations"]

                # Save report to database
                try:
                    research = (
                        db_session.query(ResearchHistory)
                        .filter_by(id=research_id)
                        .first()
                    )

                    if not research:
                        logger.error(f"Research {research_id} not found")
                        success = False
                    else:
                        research.report_content = formatted_content
                        if research.research_meta:
                            research.research_meta.update(metadata)
                        else:
                            research.research_meta = metadata
                        db_session.commit()
                        success = True
                        logger.info(
                            f"Saved report for research {research_id} to database"
                        )
                except Exception:
                    logger.exception("Error saving report to database")
                    db_session.rollback()
                    success = False

                if not success:
                    raise RuntimeError("Failed to save research report")  # noqa: TRY301 — triggers research failure handling in outer except

                logger.info(
                    f"Report saved to database for research_id: {research_id}"
                )

            # Update research status in database
            completed_at = datetime.now(UTC).isoformat()

            with get_user_db_session(username) as db_session:
                research = (
                    db_session.query(ResearchHistory)
                    .filter_by(id=research_id)
                    .first()
                )

                # Preserve existing metadata and merge with report metadata
                metadata = _parse_research_metadata(research.research_meta)

                metadata.update(final_report["metadata"])
                metadata["iterations"] = results["iterations"]

                # Use the helper function for consistent duration calculation
                duration_seconds = calculate_duration(
                    research.created_at, completed_at
                )

                research.status = ResearchStatus.COMPLETED
                research.completed_at = completed_at
                research.duration_seconds = duration_seconds
                # Note: report_content is saved by CachedResearchService
                # report_path is not used in encrypted database version

                # Generate headline and topics only for news searches
                if (
                    metadata.get("is_news_search")
                    or metadata.get("search_type") == "news_analysis"
                ):
                    try:
                        from ..news.utils.headline_generator import (
                            generate_headline,  # type: ignore[no-redef]
                        )
                        from ..news.utils.topic_generator import (
                            generate_topics,  # type: ignore[no-redef]
                        )

                        # Get the report content from database for better headline/topic generation
                        report_content = ""
                        try:
                            research = (
                                db_session.query(ResearchHistory)
                                .filter_by(id=research_id)
                                .first()
                            )
                            if research and research.report_content:
                                report_content = research.report_content
                            else:
                                logger.warning(
                                    f"No report content found in database for research_id: {research_id}"
                                )
                        except Exception:
                            logger.warning(
                                "Could not retrieve report content from database"
                            )

                        # Generate headline
                        headline = generate_headline(query, report_content)
                        metadata["generated_headline"] = headline

                        # Generate topics
                        topics = generate_topics(
                            query=query,
                            findings=report_content,
                            category=metadata.get("category", "News"),
                            max_topics=6,
                        )
                        metadata["generated_topics"] = topics

                        logger.info(f"Generated headline: {headline}")
                        logger.info(f"Generated topics: {topics}")

                    except Exception:
                        logger.warning("Could not generate headline/topics")

                research.research_meta = metadata

                db_session.commit()

                # Update subscription if this was triggered by a subscription
                if metadata.get("subscription_id"):
                    try:
                        from ...news.subscription_manager.storage import (
                            SQLSubscriptionStorage,
                        )
                        from datetime import datetime as dt, timezone, timedelta

                        sub_storage = SQLSubscriptionStorage(db_session)
                        subscription_id = metadata["subscription_id"]

                        # Get subscription to find refresh interval
                        subscription = sub_storage.get(subscription_id)
                        if subscription:
                            refresh_minutes = subscription.get(
                                "refresh_minutes", 240
                            )
                            now = dt.now(timezone.utc)
                            next_refresh = now + timedelta(
                                minutes=refresh_minutes
                            )

                            # Update refresh times
                            sub_storage.update_refresh_time(
                                subscription_id=subscription_id,
                                last_refresh=now,
                                next_refresh=next_refresh,
                            )

                            # Increment stats
                            sub_storage.increment_stats(subscription_id, 1)

                            logger.info(
                                f"Updated subscription {subscription_id} refresh times"
                            )
                    except Exception:
                        logger.warning(
                            "Could not update subscription refresh time"
                        )

            progress_callback(
                "Research completed successfully",
                100,
                {"phase": "complete"},
            )

            # Clean up resources
            # Full-report success path: the DB status was already committed
            # as COMPLETED at line ~1501, so do NOT pass final_status here.
            # Passing SUSPENDED would override the real status in the final
            # socket message, mislabeling a completed research as "Cancelled"
            # in the UI. Pass None to let cleanup_research_resources read the
            # committed COMPLETED status from the DB (same behaviour as the
            # quick-summary success path).
            cleanup_research_resources(
                research_id,
                username,
                user_password=user_password,
                final_status=None,
            )

    except ResearchTerminatedException:
        logger.info(f"Research {research_id} terminated by user")
        # handle_termination() was already called by progress_callback
        # before raising, which:
        #   1. Queued SUSPENDED status update via queue_processor
        #   2. Called cleanup_research_resources()
        # No additional cleanup needed here.

    except Exception as e:
        # Handle error
        error_message = f"Research failed: {e!s}"
        logger.exception(error_message)

        try:
            # Check for common Ollama error patterns in the exception and provide more user-friendly errors
            user_friendly_error = str(e)
            error_context = {}

            if "Error type: ollama_unavailable" in user_friendly_error:
                user_friendly_error = "Ollama AI service is unavailable. Please check that Ollama is running properly on your system."
                error_context = {
                    "solution": "Start Ollama with 'ollama serve' or check if it's installed correctly."
                }
            elif "Error type: model_not_found" in user_friendly_error:
                user_friendly_error = "Required Ollama model not found. Please pull the model first."
                error_context = {
                    "solution": "Run 'ollama pull mistral' to download the required model."
                }
            elif "Error type: connection_error" in user_friendly_error:
                user_friendly_error = "Connection error with LLM service. Please check that your AI service is running."
                error_context = {
                    "solution": "Ensure Ollama or your API service is running and accessible."
                }
            elif "Error type: api_error" in user_friendly_error:
                # Keep the original error message as it's already improved
                error_context = {
                    "solution": "Check API configuration and credentials."
                }
            # OpenAI-compatible runtime tokens (#3878). The friendly message
            # built by friendly_openai_compatible_error() already names the
            # provider, base URL, and model -- keep it as-is.
            elif "Error type: openai_connection_refused" in user_friendly_error:
                error_context = {
                    "solution": "Start your LLM server (LM Studio / vLLM / llama.cpp server) and verify the base URL in Settings -> LLM Providers."
                }
            elif "Error type: openai_timeout" in user_friendly_error:
                error_context = {
                    "solution": "The server is reachable but slow -- it may be loading a model. Retry, or increase the request timeout."
                }
            elif "Error type: openai_auth" in user_friendly_error:
                error_context = {
                    "solution": "Set or correct the API key for this provider in Settings -> LLM Providers. Local servers usually accept any non-empty key."
                }
            elif "Error type: openai_permission_denied" in user_friendly_error:
                error_context = {
                    "solution": "Your API key is valid but lacks access to this model. Pick a model your account/server is permitted to use."
                }
            elif "Error type: openai_model_not_found" in user_friendly_error:
                error_context = {
                    "solution": "The model id is not loaded on this server. Pick a currently-loaded model in the provider's UI/config."
                }
            elif "Error type: openai_bad_request" in user_friendly_error:
                error_context = {
                    "solution": "The server rejected the request. Check the model id and any provider-specific parameters."
                }
            elif "Error type: openai_unknown" in user_friendly_error:
                error_context = {
                    "solution": "Check the provider's logs for the full error and verify the base URL / model id."
                }
            elif "Error type: openai_rate_limit" in user_friendly_error:
                error_context = {
                    "solution": "The provider rate-limited the request. Wait a moment and retry, or enable LLM Rate Limiting in Settings."
                }

            # Generate enhanced error report for failed research
            enhanced_report_content = None
            try:
                # Get partial results if they exist
                partial_results = results if "results" in locals() else None
                search_iterations = (
                    results.get("iterations", 0) if partial_results else 0
                )

                # Generate comprehensive error report
                # ErrorReportGenerator does not use LLM (kept for compat)
                error_generator = ErrorReportGenerator()
                enhanced_report_content = error_generator.generate_error_report(
                    error_message=f"Research failed: {e!s}",
                    query=query,
                    partial_results=partial_results,
                    search_iterations=search_iterations,
                    research_id=research_id,
                )

                logger.info(
                    "Generated enhanced error report for failed research (length: {})",
                    len(enhanced_report_content),
                )

                # Save enhanced error report to encrypted database
                try:
                    # username already available from function scope (line 281)
                    if username:
                        from ...storage import get_report_storage

                        with get_user_db_session(username) as db_session:
                            storage = get_report_storage(session=db_session)
                            success = storage.save_report(
                                research_id=research_id,
                                content=enhanced_report_content,
                                metadata={"error_report": True},
                                username=username,
                            )
                            if success:
                                logger.info(
                                    "Saved enhanced error report to encrypted database for research {}",
                                    research_id,
                                )
                            else:
                                logger.warning(
                                    "Failed to save enhanced error report to database for research {}",
                                    research_id,
                                )
                    else:
                        logger.warning(
                            "Cannot save error report: username not available"
                        )

                except Exception as report_error:
                    logger.exception(
                        "Failed to save enhanced error report: {}", report_error
                    )

            except Exception as error_gen_error:
                logger.exception(
                    "Failed to generate enhanced error report: {}",
                    error_gen_error,
                )
                enhanced_report_content = None

            # Get existing metadata from database first
            existing_metadata = {}
            try:
                # username already available from function scope (line 281)
                if username:
                    with get_user_db_session(username) as db_session:
                        research = (
                            db_session.query(ResearchHistory)
                            .filter_by(id=research_id)
                            .first()
                        )
                        if research and research.research_meta:
                            existing_metadata = dict(research.research_meta)
            except Exception:
                logger.exception("Failed to get existing metadata")

            # Update metadata with more context about the error while preserving existing values
            metadata = existing_metadata
            metadata.update({"phase": "error", "error": user_friendly_error})
            if error_context:
                metadata.update(error_context)
            if enhanced_report_content:
                metadata["has_enhanced_report"] = True

            # If we still have an active research record, update its log
            if is_research_active(research_id):
                progress_callback(user_friendly_error, None, metadata)

            # We reached the generic exception handler, which means a
            # real error occurred (a clean user-cancel is handled by the
            # earlier `except ResearchTerminatedException` block, not
            # here). Mark FAILED unconditionally so the history view
            # can distinguish "research errored out" from "user
            # cancelled" — even if the user happened to click cancel
            # while this error was in flight, the real error is the
            # more informative cause.
            status = ResearchStatus.FAILED
            message = user_friendly_error

            # Calculate duration up to termination point - using UTC consistently
            now = datetime.now(UTC)
            completed_at = now.isoformat()

            # NOTE: Database updates from threads are handled by queue processor
            # The queue_processor.queue_error_update() method is already being used below
            # to safely update the database from the main thread

            # Queue the error update to be processed in main thread
            # Using the queue processor v2 system
            from ..queue.processor_v2 import queue_processor

            if username:
                queue_processor.queue_error_update(
                    username=username,
                    research_id=research_id,
                    status=status,
                    error_message=message,
                    metadata=metadata,
                    completed_at=completed_at,
                    report_path=None,
                )
                logger.info(
                    f"Queued error update for research {research_id} with status '{status}'"
                )
            else:
                logger.error(
                    f"Cannot queue error update for research {research_id} - no username provided. "
                    f"Status: '{status}', Message: {message}"
                )

            try:
                SocketIOService().emit_to_subscribers(
                    "progress",
                    research_id,
                    {"status": status, "error": message},
                )
            except Exception:
                logger.exception("Failed to emit error via socket")

        except Exception:
            logger.exception("Error in error handler")

        # Clean up resources
        cleanup_research_resources(
            research_id,
            username,
            user_password=user_password,
            final_status=ResearchStatus.FAILED,
        )

    finally:
        # [PERF] emit overall wall-clock on every exit path. Cheap (one
        # log line) and arms the after-the-fact analyses that grep
        # ``event=summary`` — paired with per-stage begin/end it shows
        # whether stages sum close to total (they should; gaps are
        # un-instrumented Python work like the per-iteration LLM calls
        # inside analyze_topic).
        try:
            logger.info(
                f"[PERF] research={research_id} stage=overall event=summary "
                f"total_s={time.monotonic() - _t_overall_start:.3f}"
            )
        except Exception:
            logger.debug("Failed to emit [PERF] overall summary", exc_info=True)

        # RESOURCE CLEANUP: Close search engine HTTP sessions.
        #
        # Search engines (created via get_search()) may hold HTTP connection
        # pools. Currently only SemanticScholarSearchEngine creates a
        # persistent SafeSession; other engines use stateless safe_get()/
        # safe_post() utility functions. However, BaseSearchEngine.close()
        # is safe to call on any engine — it checks for a 'session'
        # attribute and is fully idempotent (SemanticScholar sets
        # self.session = None after close).
        #
        # Neither @thread_cleanup nor cleanup_research_resources() close
        # the search engine — @thread_cleanup only handles database sessions
        # and context cleanup, and cleanup_research_resources() only handles
        # status updates, notifications, and tracking dict removal.
        #
        # Without this explicit close, search engine sessions rely on
        # Python's non-deterministic garbage collection (__del__) for
        # cleanup, which can cause file descriptor exhaustion under
        # sustained load.
        from ...utilities.resource_utils import safe_close

        if "use_search" in locals():
            safe_close(use_search, "research search engine")
        # Close search system (cascades to strategy thread pools).
        # See AdvancedSearchSystem.close() for details.
        if "system" in locals():
            safe_close(system, "research system")
        # Close the LLM instance created for model/provider overrides.
        # system.close() does NOT close the LLM passed to it via system.model,
        # so we must close it explicitly here.
        if "use_llm" in locals():
            safe_close(use_llm, "research LLM")


def cleanup_research_resources(
    research_id, username=None, user_password=None, final_status=None
):
    """
    Clean up resources for a completed research.

    Args:
        research_id: The ID of the research
        username: The username for database access (required for thread context)
        user_password: Optional decryption password for the user's DB
        final_status: Optional explicit final status the caller already
            knows (e.g. SUSPENDED from the cancel path, FAILED from the
            error path). Used because handle_termination() and the error
            handler queue the DB status update asynchronously via
            processor_v2 — by the time we reach this function the queue
            may not have been processed yet, so a fresh DB read would
            still see ``in_progress``. When None, falls back to reading
            from DB, then to COMPLETED.
    """
    from ..routes.globals import cleanup_research

    logger.info("Cleaning up resources for research {}", research_id)

    # For testing: Add a small delay to simulate research taking time
    # This helps test concurrent research limits
    from ...settings.env_registry import is_test_mode

    if is_test_mode():
        import time

        logger.info(
            f"Test mode: Adding 5 second delay before cleanup for {research_id}"
        )
        time.sleep(5)

    # Determine the final status for the socket message.
    #
    # Preference order:
    #   1. Explicit ``final_status`` from caller — most reliable; the
    #      caller knows which path it came from before any async queue
    #      processing.
    #   2. Latest committed status in the DB — best-effort fallback for
    #      legacy callers that don't pass an explicit hint.
    #   3. COMPLETED — last-resort default for the success path.
    #
    # Without this, the socket emit below sends
    # ``{status: COMPLETED, message: "Research process has ended..."}``
    # for every cleanup path — including the cancel and error paths
    # whose status was queued as SUSPENDED / FAILED. That bogus
    # "completed" socket message then overwrites the real status in the
    # browser UI, leaving cancelled / failed research mislabelled as
    # "Completed".
    current_status = final_status
    if current_status is None and username:
        try:
            with get_user_db_session(username) as db_session:
                row = (
                    db_session.query(ResearchHistory)
                    .filter_by(id=research_id)
                    .first()
                )
                if row and row.status:
                    current_status = row.status
        except Exception:
            logger.exception(
                f"Could not read current status for research {research_id}; "
                f"falling back to COMPLETED"
            )
    if current_status is None:
        current_status = ResearchStatus.COMPLETED

    # NOTE: Queue processor already handles database updates from the main thread
    # The notify_research_completed() method is called at the end of this function
    # which safely updates the database status

    # Notify queue processor that research completed
    # This uses processor_v2 which handles database updates in the main thread
    # avoiding the Flask request context issues that occur in background threads
    from ..queue.processor_v2 import queue_processor

    if username:
        queue_processor.notify_research_completed(
            username, research_id, user_password=user_password
        )
        logger.info(
            f"Notified queue processor of completion for research {research_id} (user: {username})"
        )
    else:
        logger.warning(
            f"Cannot notify completion for research {research_id} - no username provided"
        )

    # Remove from active research and termination flags atomically
    cleanup_research(research_id)

    # Clean up throttle state for this research
    with _last_emit_lock:
        _last_emit_times.pop(research_id, None)

    # Send a final message to subscribers
    try:
        # Send a final message to any remaining subscribers with explicit status
        # Use the proper status message based on database status
        if current_status == ResearchStatus.SUSPENDED:
            final_message = {
                "status": current_status,
                "message": _("Research cancelled by user."),
                "progress": 0,
            }
        elif current_status == ResearchStatus.FAILED:
            final_message = {
                "status": current_status,
                "message": _("Research failed due to an error."),
                "progress": 0,
            }
        else:
            final_message = {
                "status": ResearchStatus.COMPLETED,
                "message": _("Research process has ended and resources have been cleaned up"),
                "progress": 100,
            }

        logger.info(
            "Sending final {} socket message for research {}",
            current_status,
            research_id,
        )

        SocketIOService().emit_to_subscribers(
            "progress", research_id, final_message
        )

        # Clean up socket subscriptions for this research
        SocketIOService().remove_subscriptions_for_research(research_id)

    except Exception:
        logger.exception("Error sending final cleanup message")


def handle_termination(research_id, username=None):
    """
    Handle the termination of a research process.

    Args:
        research_id: The ID of the research
        username: The username for database access (required for thread context)
    """
    logger.info(f"Handling termination for research {research_id}")

    # Queue the status update to be processed in the main thread
    # This avoids Flask request context errors in background threads
    try:
        from ..queue.processor_v2 import queue_processor

        now = datetime.now(UTC)
        completed_at = now.isoformat()

        # Queue the suspension update
        queue_processor.queue_error_update(
            username=username,
            research_id=research_id,
            status=ResearchStatus.SUSPENDED,
            error_message="Research was terminated by user",
            metadata={"terminated_at": completed_at},
            completed_at=completed_at,
            report_path=None,
        )

        logger.info(f"Queued suspension update for research {research_id}")
    except Exception:
        logger.exception(
            f"Error queueing termination update for research {research_id}"
        )

    # Clean up resources (this already handles things properly)
    cleanup_research_resources(
        research_id, username, final_status=ResearchStatus.SUSPENDED
    )


def cancel_research(research_id, username):
    """
    Cancel/terminate a research process using ORM.

    Args:
        research_id: The ID of the research to cancel
        username: The username of the user cancelling the research

    Returns:
        bool: True if the research was found and cancelled, False otherwise
    """
    try:
        from ..routes.globals import is_research_active, set_termination_flag

        # Set termination flag
        set_termination_flag(research_id)

        # Check if the research is active
        if is_research_active(research_id):
            # Call handle_termination to update database
            handle_termination(research_id, username)
            return True
        try:
            with get_user_db_session(username) as db_session:
                research = (
                    db_session.query(ResearchHistory)
                    .filter_by(id=research_id)
                    .first()
                )
                if not research:
                    logger.info(f"Research {research_id} not found in database")
                    return False

                # Check if already in a terminal state
                if research.status in (
                    ResearchStatus.COMPLETED,
                    ResearchStatus.SUSPENDED,
                    ResearchStatus.FAILED,
                    ResearchStatus.ERROR,
                ):
                    logger.info(
                        f"Research {research_id} already in terminal state: {research.status}"
                    )
                    return True  # Consider this a success since it's already stopped

                # If it exists but isn't in active_research, still update status
                research.status = ResearchStatus.SUSPENDED
                db_session.commit()
                logger.info(f"Successfully suspended research {research_id}")
        except Exception:
            logger.exception(
                f"Error accessing database for research {research_id}"
            )
            return False

        return True
    except Exception:
        logger.exception(
            f"Unexpected error in cancel_research for {research_id}"
        )
        return False

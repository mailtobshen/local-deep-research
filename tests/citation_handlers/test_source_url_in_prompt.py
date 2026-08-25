"""Source entries must carry their URL so the LLM can cite real links.

Observed 2026-08-25 (research 9fd73401, 17:45): ``_format_sources``
renders each source as ``[N] {content}`` — NO title, NO URL. But the
analyze_followup prompt REQUIRES hyperlinked citations
``[[N]](url)`` with "copy the exact URL from the source entry you are
citing". With no URL in the entry, the LLM fabricated URLs; every
citation then failed enforce's ``url_is_kept`` canonical match and was
orphan-dropped — 6 inline markers, 0 survivors, rebuilt_empty
rows_in=95, and a report with no references section at all.

Fix: each source entry gains a header line
``[N] {title} — {url}`` before its content, so the number, the URL,
and the content appear together and the LLM can copy a real URL.
"""

from local_deep_research.citation_handlers.base_citation_handler import (  # noqa: F401
    BaseCitationHandler,
)


def _format_via_subclass(docs):
    """Call _format_sources through a minimal concrete subclass."""
    from local_deep_research.citation_handlers.base_citation_handler import (
        BaseCitationHandler,
    )

    class Impl(BaseCitationHandler):
        def analyze_initial(self, query, search_results):
            return {}

        def analyze_followup(
            self, question, search_results, previous_knowledge, nr_of_links
        ):
            return {}

    h = Impl(llm=None, settings_snapshot={})
    return h._format_sources(docs)


class TestFormatSourcesCarriesURL:
    def test_entry_contains_url(self):
        from langchain_core.documents import Document

        docs = [
            Document(
                page_content="content about drones",
                metadata={
                    "source": "http://real.onion/page",
                    "title": "Real Page",
                    "index": 1,
                },
            )
        ]
        out = _format_via_subclass(docs)
        assert "http://real.onion/page" in out, out
        assert "Real Page" in out, out
        assert "[1]" in out

    def test_all_entries_numbered_with_urls(self):
        from langchain_core.documents import Document

        docs = [
            Document(
                page_content=f"c{i}",
                metadata={
                    "source": f"http://s{i}.onion/x",
                    "title": f"T{i}",
                    "index": i,
                },
            )
            for i in (1, 2, 3)
        ]
        out = _format_via_subclass(docs)
        for i in (1, 2, 3):
            assert f"http://s{i}.onion/x" in out

    def test_content_still_present(self):
        from langchain_core.documents import Document

        docs = [
            Document(
                page_content="drone frame content",
                metadata={
                    "source": "http://x.onion/",
                    "title": "X",
                    "index": 7,
                },
            )
        ]
        out = _format_via_subclass(docs)
        assert "drone frame content" in out

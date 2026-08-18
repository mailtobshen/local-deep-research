"""CJK (Chinese) query utilities for the darkweb search engine.

Why this exists
---------------
In the 2026-08-18 incident (``research b8bc155f-…``), the agent's
first ``web_search`` call on a long Chinese query returned
``"No results found"`` from the darkweb SearXNG sidecar (ahmia /
torch). Long CJK queries index poorly in those engines — short
English keyword queries are what ahmia/torch's indexes are built
on. As a result the entire research produced an empty
``SearchResultsCollector`` and a hallucinated report with no real
sources.

This module provides lightweight query planning so that whenever a
darkweb (or any tor-egress) ``web_search`` call lands on a Chinese
query, the strategy can fan out into multiple smaller queries — some
Chinese short phrases, some English aliases drawn from a tiny
darkweb-domain keyword map — instead of betting everything on the
original long string.

Design constraints
------------------
- No new dependencies (the project does not ship ``jieba`` and the
  user has not asked for one). Tokenisation falls back to a regex
  split on CJK punctuation + a stopword list + length-windowed
  fragments.
- The functions are pure and side-effect-free so they can be unit-
  tested without touching SearXNG or any LLM.
- ``plan_darkweb_queries`` is the only public entry point consumed
  by the strategy. Everything else is an implementation detail.

What this module does NOT do
----------------------------
- It does not translate Chinese to English by itself — it only maps
  known darkweb-domain keywords to canned English aliases. General
  CJK→EN translation is left to the LLM downstream if needed.
- It does not know which search engine is being used. The caller
  (``_make_web_search_tool`` in langgraph_agent_strategy.py) gates
  the multi-query fan-out on ``search_engine_name == "darkweb"``
  and on the presence of CJK characters. This module never inspects
  the engine name.
"""

from __future__ import annotations

import re
from typing import List


# CJK Unified Ideographs basic + extensions A–G cover everyday
# Simplified Chinese. We deliberately include the full range so the
# detector is not fooled by rare / traditional characters. The same
# range is already used by ``citation_formatter``'s
# ``_SOURCES_SECTION_CJK_PATTERNS`` so the project has a consistent
# notion of what counts as CJK.
_CJK_CHAR_RE = re.compile(
    "["
    "一-鿿"  # CJK Unified Ideographs (basic)
    "㐀-䶿"  # CJK Unified Ideographs Extension A
    "\U00020000-\U0002a6df"  # Extension B
    "\U0002a700-\U0002ebef"  # Extension C–F
    "豈-﫿"  # CJK Compatibility Ideographs
    "\U0002f800-\U0002fa1f"  # CJK Compatibility Supplement
    "]"
)


# Darkweb-domain keywords and their canonical English search terms.
# Deliberately small and conservative — we are not building a
# translator, just enough mapping so a Chinese "fentanyl trafficking"
# query becomes a usable English short phrase alongside the original
# Chinese phrase. Aliases are intentionally short (1–3 words) because
# ahmia/torch index on keyword-style queries.
_DARKWEB_KEYWORD_ALIASES: dict[str, tuple[str, ...]] = {
    # opioid family
    "芬太尼": ("fentanyl",),
    "吗啡": ("morphine",),
    "海洛因": ("heroin",),
    "鸦片": ("opium",),
    "阿片": ("opioids",),
    "可卡因": ("cocaine",),
    "冰毒": ("methamphetamine",),
    "甲基苯丙": ("methamphetamine",),
    # routes / market
    "贩运": ("trafficking",),
    "贩毒": ("drug trafficking",),
    "走私": ("smuggling",),
    "交易": ("trade",),
    "市场": ("market",),
    "暗网": ("darknet",),
    "暗网市场": ("darknet market",),
    "网络": ("network",),
    "路由": ("route",),
    "执法": ("enforcement",),
    # drug classes
    "精神药物": ("psychedelic",),
    "致幻剂": ("psychedelic",),
    "合成": ("synthetic",),
    "兴奋剂": ("stimulant",),
    "镇静": ("sedative",),
    # financial
    "比特币": ("bitcoin",),
    "加密货币": ("cryptocurrency",),
    "洗钱": ("laundering",),
    # orgs / ops
    "组织": ("cartel",),
    "团伙": ("syndicate",),
    "集团": ("syndicate",),
    "国际": ("international",),
    # years (kept as-is when found)
    "2024": ("2024",),
    "2025": ("2025",),
    "2026": ("2026",),
}


# Stopwords for the Chinese short-phrase splitter. These are the
# characters most likely to add noise to a 4–6 character phrase
# generated from a longer query.
_CJK_STOPWORDS = set("的与和及或是在了我有不把了个一些一二三四五六七八九十")


# Split on ASCII whitespace, Chinese commas/periods/semicolons/
# colons/em-dashes, full-width punctuation, and the CJK enumeration
# comma (、). We deliberately do NOT split on the empty Chinese
# particles (的/了) so common natural phrases survive intact.
_SPLIT_PUNCT_RE = re.compile(
    r"["
    r"\s"  # ASCII whitespace
    r"　"  # ideographic space
    r",，、;；:：.。!?！？~～—/／"  # Chinese & ASCII punctuation
    r"()（）【】\[\]"  # brackets
    r"'"  # smart quotes
    r"\-"  # ASCII hyphen; placed last so it's not parsed as range
    r"]+"
)


def contains_cjk(text: str) -> bool:
    """Return True iff *text* contains at least one CJK ideograph.

    Args:
        text: Any string. Empty strings and ASCII-only strings return
            False.

    Returns:
        True iff ``_CJK_CHAR_RE.search(text)`` matches.
    """
    if not text:
        return False
    return _CJK_CHAR_RE.search(text) is not None


def _strip_stopwords_phrase(phrase: str) -> str:
    """Remove leading/trailing CJK stopword characters from *phrase*.

    Keeps mid-phrase particles intact (we only strip at the edges) so
    compound terms like "国际贩运" stay together.
    """
    if not phrase:
        return phrase
    chars = list(phrase)
    # left edge
    while chars and chars[0] in _CJK_STOPWORDS:
        chars.pop(0)
    # right edge
    while chars and chars[-1] in _CJK_STOPWORDS:
        chars.pop()
    return "".join(chars)


def split_cjk_phrases(query: str, max_phrases: int = 4) -> List[str]:
    """Split a CJK-containing *query* into up to *max_phrases* phrases.

    Strategy:
        1. Split on punctuation + whitespace.
        2. Drop empties and pure-stopword fragments.
        3. Prefer the longest fragments first (they carry the most
           semantics), then fill with shorter ones until
           ``max_phrases`` is reached or fragments run out.
        4. Strip stopwords at the edges of each kept fragment.

    Args:
        query: Raw query string. If it contains no CJK, this function
            returns ``[]`` (the caller is expected to short-circuit
            with ``contains_cjk(query)``).
        max_phrases: Hard cap on returned phrases (default 4).

    Returns:
        De-duplicated list of phrases, longest first. Returns ``[]``
        when no usable fragment survives cleaning.
    """
    if not query or not contains_cjk(query):
        return []
    raw_fragments = _SPLIT_PUNCT_RE.split(query)
    cleaned: List[str] = []
    seen: set[str] = set()
    for frag in raw_fragments:
        stripped = frag.strip()
        if not stripped:
            continue
        # Must still contain CJK after stripping — pure-ASCII
        # fragments (years, English words) are handled by the alias
        # step downstream.
        if not contains_cjk(stripped):
            continue
        cleaned_phrase = _strip_stopwords_phrase(stripped)
        if not cleaned_phrase:
            continue
        if cleaned_phrase in seen:
            continue
        seen.add(cleaned_phrase)
        cleaned.append(cleaned_phrase)
    # Longest first so the most informative short queries win the
    # ``max_phrases`` cap.
    cleaned.sort(key=len, reverse=True)
    return cleaned[:max_phrases]


def darkweb_english_aliases(zh_phrase: str) -> List[str]:
    """Translate one *zh_phrase* to a list of English search aliases.

    Walks the phrase left-to-right, greedily matching against
    ``_DARKWEB_KEYWORD_ALIASES`` keys sorted by length (longest
    match wins, so "暗网市场" beats "暗网"). Each matched keyword
    contributes its first alias to the output. Phrase fragments with
    no mapping are skipped (we do not invent translations).

    Args:
        zh_phrase: A Chinese short phrase. Typically the output of
            ``split_cjk_phrases``.

    Returns:
        Deduplicated list of English alias strings. Empty when no
        keyword in the phrase has a mapping.
    """
    if not zh_phrase or not contains_cjk(zh_phrase):
        return []
    # Greedy scan, longest-key-first.
    keys_by_len = sorted(
        _DARKWEB_KEYWORD_ALIASES.keys(),
        key=len,
        reverse=True,
    )
    out: List[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(zh_phrase):
        matched = False
        for key in keys_by_len:
            if zh_phrase.startswith(key, i):
                alias = _DARKWEB_KEYWORD_ALIASES[key][0]
                if alias not in seen:
                    seen.add(alias)
                    out.append(alias)
                i += len(key)
                matched = True
                break
        if not matched:
            i += 1
    return out


def plan_darkweb_queries(query: str, max_queries: int = 4) -> List[str]:
    """Plan the multi query list sequence to issue for a darkweb search.

    Pipeline:
        1. ``split_cjk_phrases(query)`` — up to 4 Chinese short phrases.
        2. For each phrase, ``darkweb_english_aliases`` — English
           aliases drawn from the keyword map.
        3. Combine Chinese phrases + English aliases into a single
           de-duplicated ordered list, longest queries first, capped
           at ``max_queries``.

    Args:
        query: Original user query.
        max_queries: Hard cap on the returned list (default 4). Set
            higher in callers that want more aggressive fan-out.

    Returns:
        Ordered list of query strings to issue against SearXNG. Returns
        ``[]`` when *query* contains no CJK (caller falls back to the
        original single-query path) or when no usable split survives.
        The original query is **not** in the returned list — the
        caller is expected to use it as the fallback when the plan
        is empty.
    """
    if not query or not contains_cjk(query):
        return []
    zh_phrases = split_cjk_phrases(query)
    if not zh_phrases:
        return []
    # Build candidate list: Chinese phrases first (preserving
    # longest-first order), then English aliases for each phrase in
    # the same order. De-duplicate while preserving order.
    seen: set[str] = set()
    plan: List[str] = []
    for zh in zh_phrases:
        if zh not in seen:
            seen.add(zh)
            plan.append(zh)
    for zh in zh_phrases:
        for alias in darkweb_english_aliases(zh):
            if alias not in seen:
                seen.add(alias)
                plan.append(alias)
    return plan[:max_queries]
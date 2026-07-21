"""
Lightweight translation module for the web interface.

Loads JSON translation dictionaries and provides a gettext-style _()
function for both Python (Jinja2 templates) and JavaScript (via injected
global dictionary).
"""

import json
from pathlib import Path
from typing import Any

from flask import g, request, session


_TRANSLATIONS_DIR = Path(__file__).parent
_DEFAULT_LANGUAGE = "zh"
_SUPPORTED_LANGUAGES = ["zh", "en"]


def _load_dict(lang: str) -> dict[str, str]:
    """Load translation dictionary for the given language."""
    path = _TRANSLATIONS_DIR / f"{lang}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


class Translator:
    """Simple translator that loads JSON dictionaries."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, str]] = {}

    def _get_dict(self, lang: str) -> dict[str, str]:
        if lang not in self._cache:
            self._cache[lang] = _load_dict(lang)
        return self._cache[lang]

    def gettext(self, text: str, **kwargs: Any) -> str:
        """Translate *text* into the current language.

        Supports simple ``{name}`` interpolation via *kwargs*.
        """
        lang = self.current_language
        translated = self._get_dict(lang).get(text, text)
        if kwargs:
            try:
                translated = translated.format(**kwargs)
            except KeyError:
                pass
        return translated

    @property
    def current_language(self) -> str:
        """Return the active language code.

        Resolution order:
        1. Query parameter ``?lang=``
        2. Flask session ``session["locale"]``
        3. Cookie ``locale``
        4. Default ``zh``

        Note: Accept-Language header is intentionally ignored so that
        the default language is always Simplified Chinese for new users.
        """
        try:
            # 1. Query param
            lang = request.args.get("lang")
            if lang in _SUPPORTED_LANGUAGES:
                return lang

            # 2. Session
            lang = session.get("locale")
            if lang in _SUPPORTED_LANGUAGES:
                return lang

            # 3. Cookie
            lang = request.cookies.get("locale")
            if lang in _SUPPORTED_LANGUAGES:
                return lang
        except RuntimeError:
            # Outside request context (e.g. CLI, background tasks)
            pass

        return _DEFAULT_LANGUAGE


translator = Translator()
gettext = translator.gettext
_ = gettext

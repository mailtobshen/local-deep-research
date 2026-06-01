"""
Utilities for rendering templates.
"""

from typing import Any

from flask import render_template

from ...__version__ import __version__
from ..translations import translator


def render_template_with_defaults(*args: Any, **kwargs: Any) -> str:
    """
    Renders templates with some default values filled.

    Args:
        *args: Will be passed to the normal `render_template`.
        **kwargs: Will be passed to the normal `render_template`.

    Returns:
        The rendered template.

    """
    from ...database.encrypted_db import db_manager

    # Add encryption status to all templates
    kwargs["has_encryption"] = db_manager.has_encryption

    # Inject i18n utilities into every template
    kwargs["_"] = translator.gettext
    kwargs["current_language"] = translator.current_language

    return render_template(*args, version=__version__, **kwargs)

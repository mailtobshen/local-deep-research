#!/usr/bin/env python3
"""
Clean up leading/trailing spaces inside {{ _("...") }} calls in templates.
Moves spaces outside the translation call so the key is clean.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path('src/local_deep_research/web/templates')

for filepath in TEMPLATES_DIR.rglob('*.html'):
    content = filepath.read_text()
    original = content

    # Pattern: {{ _(" text") }} or {{ _(' text') }} →  {{ _("text") }}
    # Also handles trailing spaces: {{ _("text ") }} → {{ _("text") }}
    # Handles both single and double quotes
    def cleanup_spaces(match):
        prefix = match.group(1)  # {{ _(
        quote = match.group(2)   # " or '
        text = match.group(3)    # the text
        suffix = match.group(4)  # ) }}

        leading_space = ''
        trailing_space = ''

        if text.startswith(' '):
            leading_space = ' '
            text = text[1:]
        if text.endswith(' '):
            trailing_space = ' '
            text = text[:-1]

        return f'{leading_space}{prefix}{quote}{text}{quote}{suffix}{trailing_space}'

    # Match {{ _("text") }} with optional spaces around the text
    content = re.sub(
        r'(\{\{\s*_\(\s*)([""])(.+?)\2(\s*\)\s*\}\})',
        cleanup_spaces,
        content
    )

    if content != original:
        filepath.write_text(content, encoding='utf-8')
        print(f'Cleaned: {filepath.name}')

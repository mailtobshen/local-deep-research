#!/usr/bin/env python3
"""
Apply i18n wrappers to JS files.
Very conservative - only replaces clearly safe patterns.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
JS_DIR = PROJECT_ROOT / "src" / "local_deep_research" / "web" / "static" / "js"

SKIP_JS = {"i18n.js", "language_switcher.js", "theme.js", "app.js"}


def is_user_facing_text(text):
    """Check if a JS string looks user-facing."""
    text = text.strip()
    if len(text) < 2 or len(text) > 120:
        return False
    if not re.search(r'[a-zA-Z]{2,}', text):
        return False
    # Skip template literals with ${}
    if '${' in text:
        return False
    # Skip debug/internal messages
    debug_prefixes = (
        'added ', 'adding ', 'removed ', 'removing ', 'updated ', 'updating ',
        'created ', 'creating ', 'deleted ', 'deleting ', 'found ', 'finding ',
        'loaded ', 'loading ', 'saved ', 'saving ', 'rendered ', 'rendering ',
        'initialized', 'initializing', 'connected', 'connecting', 'disconnected',
        'socket', 'event:', 'listener:', 'api response', 'api request',
        'api failed', 'api returned', 'data:', 'debug:', 'log:', 'info:',
        'warning:', 'modelInput', 'selectedModel', 'selectedProvider',
        'isInitializing', 'theme ', 'rendered ', 'applied ', 'fetched ',
    )
    if text.lower().startswith(debug_prefixes):
        return False
    # Skip CSS class names and selectors
    if re.match(r'^[.#][a-zA-Z_-][\w-]*$', text):
        return False
    # Skip file paths and URLs
    if re.match(r'^[/\.]\w', text) or '://' in text:
        return False
    # Skip if just variable names or camelCase identifiers
    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', text):
        return False
    # Skip common event names and code keywords
    code_words = {'click', 'change', 'submit', 'load', 'error', 'success',
                  'DOMContentLoaded', 'keydown', 'keyup', 'keypress',
                  'mousedown', 'mouseup', 'mousemove', 'mouseover',
                  'mouseout', 'mouseenter', 'mouseleave', 'focus',
                  'blur', 'scroll', 'resize', 'hashchange', 'popstate'}
    if text.lower() in code_words:
        return False
    # Must look like natural language
    if ' ' in text:
        return True
    if len(text) > 3 and text[0].isupper():
        return True
    return False


def process_js_file(filepath):
    """Process a JS file and apply i18n wrappers."""
    content = filepath.read_text(encoding="utf-8")
    original = content
    changes = []

    # Pattern 1: alert/confirm/prompt("text")
    def replace_dialog(match):
        func = match.group(1)
        text = match.group(2)
        if is_user_facing_text(text):
            changes.append(f"{func}: {text}")
            return f'{func}(i18n.t("{text}")'
        return match.group(0)
    content = re.sub(r'\b(alert|confirm|prompt)\s*\(\s*"([^"]+)"', replace_dialog, content)

    # Pattern 2: .textContent = "text" or .innerText = "text"
    def replace_text_content(match):
        prop = match.group(1)
        text = match.group(2)
        if is_user_facing_text(text):
            changes.append(f"{prop}: {text}")
            return f'.{prop} = i18n.t("{text}")'
        return match.group(0)
    content = re.sub(r'\.(textContent|innerText)\s*=\s*"([^"]+)"', replace_text_content, content)

    # Pattern 3: console.warn/error("text") for user-facing messages
    def replace_console(match):
        text = match.group(1)
        if is_user_facing_text(text):
            changes.append(f"console: {text}")
            return f'console.{match.group(0).split("(")[0].split(".")[-1]}(i18n.t("{text}")'
        return match.group(0)
    # Only replace simple string arguments to console
    content = re.sub(r'console\.(?:warn|error)\s*\(\s*"([^"]{5,})"', replace_console, content)

    if content != original:
        filepath.write_text(content, encoding="utf-8")
        return changes
    return []


def main():
    total_changes = 0
    total_files = 0
    for filepath in sorted(JS_DIR.rglob("*.js")):
        if filepath.name in SKIP_JS:
            continue
        changes = process_js_file(filepath)
        if changes:
            total_files += 1
            total_changes += len(changes)
            print(f"{filepath.name}: {len(changes)} changes")

    print(f"\nTotal: {total_changes} changes in {total_files} files")


if __name__ == "__main__":
    main()

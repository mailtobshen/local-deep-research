#!/usr/bin/env python3
"""
Apply i18n wrappers to HTML templates.
Very conservative - only replaces clearly safe patterns.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "src" / "local_deep_research" / "web" / "templates"

# Templates to skip (already translated or special)
SKIP_TEMPLATES = {"base.html"}


def should_translate_text(text):
    """Check if text looks like user-facing UI text."""
    text = text.strip()
    if len(text) < 2 or len(text) > 80:
        return False
    # Must have letters
    if not re.search(r'[a-zA-Z]{2,}', text):
        return False
    # Skip template syntax remnants
    if '{{' in text or '{%' in text or '%}' in text or '}}' in text:
        return False
    # Skip HTML attribute fragments
    if re.search(r'\w+=["\']', text):
        return False
    # Skip debug/log messages
    debug_prefixes = (
        'added ', 'adding ', 'removed ', 'removing ', 'updated ', 'updating ',
        'created ', 'creating ', 'deleted ', 'deleting ', 'found ', 'finding ',
        'loaded ', 'loading ', 'saved ', 'saving ', 'rendered ', 'rendering ',
        'initialized', 'initializing', 'connected', 'connecting', 'disconnected',
        'socket', 'event:', 'listener:', 'api response', 'api request',
        'api failed', 'api returned', 'data:', 'debug:', 'log:', 'info:',
        'warning:', 'error:', 'modelInput', 'selectedModel', 'selectedProvider',
        'isInitializing', 'total_pages', 'aria-disabled', 'tabindex',
    )
    if text.lower().startswith(debug_prefixes):
        return False
    # Skip CSS-like text
    css_words = {'rgba', 'rgb', 'hsl', 'url(', 'px', 'em', 'rem', 'vh', 'vw'}
    if any(w in text.lower() for w in css_words):
        return False
    # Skip if just variable names or code
    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', text):
        return False
    # Skip strings that look like code comments
    if text.startswith(('// ', '/* ', '* ', '- ', '+ ')):
        return False
    # Skip file paths
    if re.match(r'^[/\.]\w', text) or '://' in text:
        return False
    # Skip HTML entities only
    if re.match(r'^&[a-zA-Z]+;$', text.strip()):
        return False
    # Must look like natural language
    # Has spaces or is a capitalized word (title/label)
    if ' ' in text:
        return True
    if text[0].isupper() and len(text) > 2:
        return True
    return False


def process_template(filepath):
    """Process a template file and apply i18n wrappers."""
    content = filepath.read_text(encoding="utf-8")
    original = content
    changes = []

    # Pattern 1: placeholder="text"
    def replace_placeholder(match):
        text = match.group(1)
        if should_translate_text(text):
            changes.append(f"placeholder: {text}")
            return f'placeholder="{{{{ _("{text}") }}}}"'
        return match.group(0)
    content = re.sub(r'placeholder=["\']([^"\']+)["\']', replace_placeholder, content)

    # Pattern 2: title="text" (but not on links with URLs)
    def replace_title(match):
        text = match.group(1)
        if should_translate_text(text) and '://' not in text:
            changes.append(f"title: {text}")
            return f'title="{{{{ _("{text}") }}}}"'
        return match.group(0)
    content = re.sub(r'title=["\']([^"\']+)["\']', replace_title, content)

    # Pattern 3: aria-label="text"
    def replace_aria(match):
        text = match.group(1)
        if should_translate_text(text):
            changes.append(f"aria-label: {text}")
            return f'aria-label="{{{{ _("{text}") }}}}"'
        return match.group(0)
    content = re.sub(r'aria-label=["\']([^"\']+)["\']', replace_aria, content)

    # Pattern 4: alt="text"
    def replace_alt(match):
        text = match.group(1)
        if should_translate_text(text):
            changes.append(f"alt: {text}")
            return f'alt="{{{{ _("{text}") }}}}"'
        return match.group(0)
    content = re.sub(r'alt=["\']([^"\']+)["\']', replace_alt, content)

    # Pattern 5: Text nodes inside common UI elements
    # Match >text< but not inside script/style tags
    # We process line by line to avoid complex regex
    lines = content.split('\n')
    new_lines = []
    in_script = False
    in_style = False
    for line in lines:
        stripped = line.strip().lower()
        if '<script' in stripped:
            in_script = True
        if '</script>' in stripped:
            in_script = False
        if '<style' in stripped:
            in_style = True
        if '</style>' in stripped:
            in_style = False

        if in_script or in_style:
            new_lines.append(line)
            continue

        # Find >text< patterns on this line
        def replace_text_node(match):
            text = match.group(1)
            if should_translate_text(text):
                # Escape quotes for Jinja2
                escaped = text.replace('"', '\\"')
                changes.append(f"text: {text[:40]}")
                return f'>{{{{ _("{escaped}") }}}}<'
            return match.group(0)

        new_line = re.sub(r'>([^<]{2,60})<', replace_text_node, line)
        new_lines.append(new_line)

    content = '\n'.join(new_lines)

    if content != original:
        filepath.write_text(content, encoding="utf-8")
        return changes
    return []


def main():
    total_changes = 0
    total_files = 0
    for filepath in sorted(TEMPLATES_DIR.rglob("*.html")):
        if filepath.name in SKIP_TEMPLATES:
            continue
        changes = process_template(filepath)
        if changes:
            total_files += 1
            total_changes += len(changes)
            print(f"{filepath.name}: {len(changes)} changes")

    print(f"\nTotal: {total_changes} changes in {total_files} files")


if __name__ == "__main__":
    main()

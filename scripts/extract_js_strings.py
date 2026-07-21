#!/usr/bin/env python3
import re
from pathlib import Path

JS_DIR = Path('src/local_deep_research/web/static/js')
all_strings = set()

for filepath in JS_DIR.rglob('*.js'):
    if filepath.name in ('i18n.js', 'language_switcher.js', 'theme.js'):
        continue
    content = filepath.read_text()

    # Pattern 1: alert/confirm/prompt with string literals
    for match in re.finditer(r"(?:alert|confirm|prompt)\s*\(\s*['\"]([^'\"]{2,})['\"]\s*\)", content):
        text = match.group(1)
        if re.search(r'[a-zA-Z]{2,}', text) and not text.startswith('http'):
            all_strings.add(text)

    # Pattern 2: .textContent = 'text' (simple strings only, no vars)
    for match in re.finditer(r"\.(?:textContent|innerText)\s*=\s*['\"]([^'\"]{2,})['\"]\s*;", content):
        text = match.group(1)
        if re.search(r'[a-zA-Z]{2,}', text) and '${' not in text:
            all_strings.add(text)

    # Pattern 3: SafeLogger with user messages
    for match in re.finditer(r"SafeLogger\.(?:log|warn|error)\s*\(\s*['\"]([^'\"]{5,})['\"]", content):
        text = match.group(1)
        if re.search(r'[a-zA-Z]{2,}', text):
            skip = ('theme', 'initialized', 'loaded', 'rendered', 'fetched',
                    'updated', 'applied', 'socket', 'event', 'dropdown')
            if not text.lower().startswith(skip):
                all_strings.add(text)

print(f'Found {len(all_strings)} unique JS strings')
for s in sorted(all_strings)[:50]:
    print(repr(s))

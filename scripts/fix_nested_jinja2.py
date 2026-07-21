import re
from pathlib import Path

templates_dir = Path('src/local_deep_research/web/templates')
fixed_count = 0

for filepath in templates_dir.rglob('*.html'):
    content = filepath.read_text()
    original = content

    # Pattern: inside help_tip strings, revert {{ _("text") }} back to text
    content = re.sub(
        r'(help_tip\([^)]*<strong>)\{\{\s*_\(\s*"([^"]+)"\s*\)\s*\}\}(</strong>)',
        r'\1\2\3',
        content
    )

    if content != original:
        filepath.write_text(content, encoding='utf-8')
        fixed_count += 1
        print(f'Fixed: {filepath.name}')

print(f'\nFixed {fixed_count} files')

"""Validate repository Markdown file links and heading anchors without network access."""

from collections import Counter
from html import unescape
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+["\'][^\n]*?["\'])?\s*\)')


def prose(text):
    """Ignore examples in fenced code blocks, retaining line numbers."""
    lines = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append('')
        else:
            lines.append('' if fence else line)
    return '\n'.join(lines)


def anchors(text):
    counts = Counter()
    found = set(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']', text))
    for match in re.finditer(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$', prose(text), re.M):
        title = re.sub(r'!?\[([^\]]+)\]\([^)]*\)', r'\1', match.group(1))
        title = re.sub(r'<[^>]*>', '', unescape(title)).lower()
        slug = re.sub(r'[^\w\- ]', '', title).replace(' ', '-')
        suffix = f'-{counts[slug]}' if counts[slug] else ''
        counts[slug] += 1
        found.add(slug + suffix)
    return found


def check(root=ROOT):
    root = Path(root).resolve()
    documents = sorted([*root.glob('*.md'), *(root / 'docs').rglob('*.md')])
    errors, count = [], 0
    for document in documents:
        text = prose(document.read_text(encoding='utf-8-sig'))
        for match in LINK.finditer(text):
            link = unescape(match.group(1) or match.group(2))
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            count += 1
            target = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
            location = f'{document.relative_to(root)}:{text[:match.start()].count(chr(10)) + 1}'
            if not target.is_relative_to(root):
                errors.append(f'{location}: link escapes repository: {link}')
            elif not target.exists():
                errors.append(f'{location}: missing target: {link}')
            elif parsed.fragment and target.suffix.lower() == '.md':
                if unquote(parsed.fragment) not in anchors(target.read_text(encoding='utf-8-sig')):
                    errors.append(f'{location}: missing heading: {link}')
    return count, errors


if __name__ == '__main__':
    checked, problems = check()
    for problem in problems:
        print(problem)
    print(f'Checked {checked} local documentation links; {len(problems)} errors.')
    sys.exit(bool(problems))

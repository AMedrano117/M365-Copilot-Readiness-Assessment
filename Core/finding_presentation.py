"""Readable finding details from existing text; no assessment or source changes."""
import re

from .evidence_reconciliation import customer_text


PAIR = re.compile(r'^([A-Za-z][A-Za-z0-9 /_()%–-]{0,55}):\s+(\S.*)$', re.S)
COUNT = re.compile(r'^(\d[\d,]*(?:\.\d+)?%?)\s+([A-Za-z][^:]*)$', re.S)
ABBREVIATIONS = {'e.g.', 'i.e.', 'etc.', 'vs.', 'mr.', 'mrs.', 'ms.', 'dr.', 'u.s.', 'u.k.'}


def statements(value):
    """Split explicit statements, retaining qualifications and ordinary commas.

    Commas separate only labeled fields or parallel numeric facts. Commas in
    numbers, normal prose, quotes, parentheses and URLs remain untouched.
    """
    text = customer_text(str(value or '')).strip()
    protected = {index for match in re.finditer(r'https?://[^\s<>]+', text)
                 for index in range(match.start(), match.end())}
    items, start, depth, quote = [], 0, 0, None
    for index, char in enumerate(text):
        if index in protected:
            continue
        if quote:
            if char == quote and (index == 0 or text[index-1] != '\\'):
                quote = None
            continue
        if char in {'"', '`'} or char == "'" and (index == 0 or not text[index-1].isalnum()):
            quote = char
            continue
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth = max(0, depth-1)
        if depth:
            continue
        following = text[index+1:]
        boundary = char in {';', '\n'}
        next_word = following.lstrip().lstrip('“"')
        if char in '.!?' and following[:1].isspace() and (next_word[:1].isupper() or next_word[:1].isdigit()):
            token = text[start:index+1].split()[-1].lower()
            boundary = (token not in ABBREVIATIONS and not re.fullmatch(r'[a-z]\.', token)
                        and not re.fullmatch(r'\d+\.', text[start:index+1].strip()))
        if char == ',' and following[:1].isspace():
            left = text[start:index].strip()
            right = following.lstrip()
            left_pair = PAIR.match(left)
            boundary = (bool(left_pair) and ':' not in left_pair.group(2) and bool(re.match(r'[A-Za-z][A-Za-z0-9 /_()%–-]{0,55}:\s+(?!//)', right))) or (
                bool(COUNT.match(left)) and bool(COUNT.match(right)))
        if boundary:
            item = text[start:index+(char in '.!?')].strip()
            if item:
                items.append(re.sub(r'^(?:[•*-]|\d+[.)])\s+', '', item))
            start = index+1
    remaining = text[start:].strip()
    if remaining:
        items.append(re.sub(r'^(?:[•*-]|\d+[.)])\s+', '', remaining))
    return items


def render_details(value, prose, table, *, steps=False):
    """Render all supplied details, escaping through the existing report helpers."""
    items = statements(value)
    if not items:
        return '<p>' + prose(value) + '</p>'
    if steps:
        if len(items) == 1:
            return '<p>' + prose(items[0]) + '</p>'
        return '<ol class="finding-steps">' + ''.join('<li>' + prose(item) + '</li>' for item in items) + '</ol>'
    blocks, position = [], 0
    while position < len(items):
        # A named population followed by parallel facts keeps that population
        # as the table caption, e.g. "MFA outcomes: Satisfied: 5, Unknown: 3".
        nested = re.match(r'^([^:\n]+):\s+(.+)$', items[position], re.S)
        if nested:
            children = statements(nested.group(2))
            child_matcher = (PAIR if len(children) >= 2 and all(PAIR.match(item) for item in children) else
                             COUNT if len(children) >= 2 and all(COUNT.match(item) for item in children) else None)
            if child_matcher:
                rows = []
                for item in children:
                    first, second = child_matcher.match(item).groups()
                    rows.append({'label': first if child_matcher is PAIR else second.rstrip('.'),
                                 'value': second if child_matcher is PAIR else first})
                blocks.append(table(rows, [('Detail', 'label'), ('Observed value', 'value')],
                                    caption=nested.group(1), mobile_labels=True))
                position += 1
                continue
        # Only consecutive, explicit key/value or numeric facts form a table;
        # free-form narrative and qualifiers retain their original order.
        matcher = PAIR if PAIR.match(items[position]) else COUNT if COUNT.match(items[position]) else None
        end = position
        if matcher:
            while end < len(items) and matcher.match(items[end]):
                end += 1
        if end-position >= 2:
            rows = []
            for item in items[position:end]:
                first, second = matcher.match(item).groups()
                rows.append({'label': first if matcher is PAIR else second.rstrip('.'),
                             'value': second if matcher is PAIR else first})
            blocks.append(table(rows, [('Detail', 'label'), ('Observed value', 'value')], mobile_labels=True))
            position = end
        else:
            # Keep adjacent prose together as a compact list.
            end = position+1
            while end < len(items) and not PAIR.match(items[end]) and not COUNT.match(items[end]):
                end += 1
            blocks.append('<ul class="finding-facts">' + ''.join('<li>' + prose(item) + '</li>' for item in items[position:end]) + '</ul>')
            position = end
    return ''.join(blocks)

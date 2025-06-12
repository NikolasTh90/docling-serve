#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
from bidi.algorithm import get_display

class Line:
    """
    A single markdown line.  Flags RTL if it contains any Arabic,
    and can reverse+BiDi-reorder its content while preserving a
    leading markdown prefix.
    """
    ARABIC_RE = re.compile(r'[\u0600-\u06FF]')

    def __init__(self, raw: str):
        self.raw = raw
        self.is_rtl = bool(self.ARABIC_RE.search(raw))

    def reversed(self) -> str:
        # capture markdown markers (#, >, *, -, etc.), body, newline
        m = re.match(
            r'^(?P<prefix>\s*(?:#{1,6}\s+|[-+*]\s+|>\s*))?'
            r'(?P<body>.*?)(?P<nl>\n?)$',
            self.raw
        )
        prefix = m.group('prefix') or ''
        body   = m.group('body')   or ''
        nl     = m.group('nl')     or ''

        # 1) reverse the mirrored input
        rev = body[::-1]
        # 2) apply full Unicode-BiDi to handle mixed runs
        bidi_fixed = get_display(rev)

        return prefix + bidi_fixed + nl


class RTLBlock:
    """Wraps consecutive RTL lines in <div dir="rtl">…</div>."""
    def __init__(self):
        self.lines = []

    def add_line(self, line: Line):
        self.lines.append(line)

    def render(self) -> str:
        out = ['<div dir="rtl">\n']
        for ln in self.lines:
            out.append(ln.reversed())
        out.append('</div>\n')
        return ''.join(out)


class MarkdownProcessor:
    """
    Walks a markdown document, groups RTL lines, and
    emits a new doc with LTR lines untouched and RTL
    blocks properly reversed+BiDi-wrapped.
    """
    def __init__(self, text: str):
        self.lines = [Line(l) for l in text.splitlines(keepends=True)]

    def process(self) -> str:
        out = []
        rtl_block = None

        for ln in self.lines:
            if ln.is_rtl:
                if rtl_block is None:
                    rtl_block = RTLBlock()
                rtl_block.add_line(ln)
            else:
                if rtl_block is not None:
                    out.append(rtl_block.render())
                    rtl_block = None
                out.append(ln.raw)

        if rtl_block is not None:
            out.append(rtl_block.render())

        return ''.join(out)


def main():
    src = sys.stdin.read()
    result = MarkdownProcessor(src).process()
    sys.stdout.write(result)


if __name__ == '__main__':
    main()
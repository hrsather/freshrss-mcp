import re
from html.parser import HTMLParser

_BLOCK = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
}
_SKIP = {"script", "style"}
# Feed text is attacker-controlled and reaches a model and terminals: drop control characters
# (keeping whitespace), bidi marks, overrides and isolates, zero-width space, word joiner, BOM and
# the invisible tag block. ZWJ and ZWNJ stay: emoji sequences and Persian text need them.
_UNSAFE_RANGES = (
    (0x00, 0x08),
    (0x0E, 0x1F),
    (0x7F, 0x9F),
    (0x061C, 0x061C),
    (0x200E, 0x200F),
    (0x200B, 0x200B),
    (0x2060, 0x2060),
    (0x202A, 0x202E),
    (0x2066, 0x2069),
    (0xFEFF, 0xFEFF),
    (0xE0000, 0xE007F),
)
_UNSAFE = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _UNSAFE_RANGES) + "]")


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(re.sub(r"\s+", " ", data))


def strip_html(html: str) -> str:
    extractor = _Extractor()
    extractor.feed(html)
    extractor.close()
    text = _UNSAFE.sub("", "".join(extractor.parts))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def clean(text: str, limit: int = 500) -> str:
    """Single-line, length-capped text from a feed or backend, without control characters."""
    return " ".join(_UNSAFE.sub("", text).split())[:limit]


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "…", True

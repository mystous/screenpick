"""터미널 표시 폭 계산 (한글 등 전각 문자는 2칸)."""

from __future__ import annotations

import unicodedata


def char_width(ch: str) -> int:
    if unicodedata.combining(ch) or ch in "​‍️":
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def width(text: str) -> int:
    return sum(char_width(c) for c in text)


def truncate(text: str, max_width: int, ellipsis: str = "…") -> str:
    """표시 폭이 max_width 를 넘지 않게 자른다. 잘렸으면 끝에 ellipsis."""
    if max_width <= 0:
        return ""
    if width(text) <= max_width:
        return text
    limit = max_width - width(ellipsis)
    out, used = [], 0
    for c in text:
        w = char_width(c)
        if used + w > limit:
            break
        out.append(c)
        used += w
    return "".join(out) + ellipsis


def crop(text: str, max_width: int) -> str:
    """말줄임표 없이 그냥 잘라 낸다 (썸네일용)."""
    out, used = [], 0
    for c in text:
        w = char_width(c)
        if used + w > max_width:
            break
        out.append(c)
        used += w
    return "".join(out)


def pad(text: str, total: int) -> str:
    return text + " " * max(0, total - width(text))


# 표준으로는 1칸이지만, 글꼴에 글자가 없어 대체 글꼴로 그리는 터미널에서는 2칸으로
# 그려져 뒤의 글자와 타일 테두리를 밀어내는 기호들. 폭이 확실한 비슷한 글자로 바꾼다.
# (예: Claude Code 의 ⎿ ⏺ ✻ ❯, 한글 글꼴에서 넓게 그려지는 — ● ①)
_SAFE_MAP = {
    "⎿": "└", "⏺": "*", "●": "*", "○": "o", "◆": "*", "◇": "o", "■": "#", "□": "#",
    "▶": ">", "◀": "<", "▸": ">", "◂": "<", "▲": "^", "▼": "v", "⏵": ">", "⏴": "<",
    "❯": ">", "›": ">", "‹": "<", "»": ">", "«": "<",
    "✔": "v", "✓": "v", "✕": "x", "✗": "x", "✘": "x", "×": "x",
    "✻": "*", "✢": "*", "✳": "*", "✶": "*", "✽": "*", "✦": "*", "✧": "*", "※": "*", "★": "*", "☆": "*",
    "⧉": "#", "—": "-", "–": "-", "―": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"', "\xa0": " ", "⚠": "!",
}
# 위 표에 없어도 이 범위의 1칸 기호는 위험하다고 보고 '·' 로 바꾼다.
_RISKY_RANGES = ((0x2300, 0x23FF), (0x2460, 0x24FF), (0x25A0, 0x25FF), (0x2600, 0x27BF),
                 (0x2900, 0x297F), (0x2B00, 0x2BFF))


def _safe_char(ch: str) -> str:
    if ch in _SAFE_MAP:
        return _SAFE_MAP[ch]
    cp = ord(ch)
    if char_width(ch) == 1 and any(lo <= cp <= hi for lo, hi in _RISKY_RANGES):
        return "·"
    return ch


def safe_symbols(text: str) -> str:
    """폭이 터미널마다 다른 기호를 바꾼다. 표시 폭은 그대로 유지한다."""
    if text.isascii():
        return text
    return "".join(_safe_char(c) for c in text)

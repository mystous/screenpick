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

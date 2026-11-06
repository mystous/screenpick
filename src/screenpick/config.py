"""설정은 모두 환경변수에서 읽는다 (Twelve-Factor III. Config).

코드에 경로나 값을 박아 두지 않고, 기본값만 여기서 정한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PREFIX = "SCREENPICK_"


@dataclass(frozen=True)
class Config:
    screen_bin: str
    tile_width: int          # 타일 최소 너비(테두리 포함). 남는 폭은 타일끼리 나눠 가진다.
    tile_height: int         # 타일 높이(테두리 포함)
    refresh_sec: float       # 자동 새로고침 주기. 0이면 끔
    capture_mode: str        # pty | hardcopy | off
    capture_timeout: float   # 세션 하나 캡처에 쓰는 최대 시간(초)
    workers: int             # 동시에 캡처할 세션 수
    attached_mode: str       # 다른 곳에 붙어 있는 세션: steal(-d -r) | share(-x)
    return_after_detach: bool  # 세션에서 detach 하면 목록으로 돌아올지
    single_instance: bool    # 새로 켜면 이전 screenpick 을 종료할지
    claude_dir: Path
    codex_dir: Path
    log_level: str


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    raw = env.get(PREFIX + key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(minimum, int(raw))
    except ValueError as exc:
        raise ValueError(f"{PREFIX}{key} 값이 정수가 아닙니다: {raw!r}") from exc


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(PREFIX + key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(0.0, float(raw))
    except ValueError as exc:
        raise ValueError(f"{PREFIX}{key} 값이 숫자가 아닙니다: {raw!r}") from exc


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(PREFIX + key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _choice(env: Mapping[str, str], key: str, default: str, choices: tuple[str, ...]) -> str:
    raw = (env.get(PREFIX + key) or default).strip().lower()
    if raw not in choices:
        raise ValueError(f"{PREFIX}{key} 값은 {'/'.join(choices)} 중 하나여야 합니다: {raw!r}")
    return raw


def load(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    home = Path(env.get("HOME", str(Path.home())))
    # claude / codex 자체가 쓰는 환경변수를 그대로 존중한다.
    claude_dir = Path(env.get(PREFIX + "CLAUDE_DIR") or env.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    codex_dir = Path(env.get(PREFIX + "CODEX_DIR") or env.get("CODEX_HOME") or home / ".codex")
    return Config(
        screen_bin=env.get(PREFIX + "SCREEN_BIN") or "screen",
        tile_width=_int(env, "TILE_WIDTH", 60, minimum=20),
        tile_height=_int(env, "TILE_HEIGHT", 14, minimum=5),
        refresh_sec=_float(env, "REFRESH_SEC", 5.0),
        capture_mode=_choice(env, "CAPTURE", "pty", ("pty", "hardcopy", "off")),
        capture_timeout=_float(env, "CAPTURE_TIMEOUT", 1.5) or 1.5,
        workers=_int(env, "WORKERS", 6),
        attached_mode=_choice(env, "ATTACHED_MODE", "steal", ("steal", "share")),
        return_after_detach=_bool(env, "RETURN_AFTER_DETACH", True),
        single_instance=_bool(env, "SINGLE_INSTANCE", True),
        claude_dir=claude_dir.expanduser(),
        codex_dir=codex_dir.expanduser(),
        log_level=(env.get(PREFIX + "LOG_LEVEL") or "WARNING").upper(),
    )

"""screen 명령으로 세션 목록과 창 정보를 얻는다."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

# 예: "\t12345.pts-0.myhost\t(11/02/26 19:16:07)\t(Detached)"
_LS_RE = re.compile(
    r"^\s+(?P<pid>\d+)\.(?P<name>\S+)\s+(?:\((?P<date>[^)]*\d[^)]*)\)\s+)?\((?P<state>[^)]+)\)\s*$"
)
# 예: "(3,45)/(178,47)+1024 -flow mouse UTF-8 0(bash)"
_INFO_RE = re.compile(r"\((\d+),(\d+)\)/\((\d+),(\d+)\)")
_INFO_WIN_RE = re.compile(r"(\d+)\(([^)]*)\)\s*$")


@dataclass(frozen=True)
class Session:
    pid: int
    name: str
    date: str
    state: str  # Attached / Detached / Dead ??? 등 screen이 보여주는 그대로

    @property
    def id(self) -> str:
        return f"{self.pid}.{self.name}"

    @property
    def attached(self) -> bool:
        return "attached" in self.state.lower()

    @property
    def dead(self) -> bool:
        return "dead" in self.state.lower()


@dataclass(frozen=True)
class WindowInfo:
    cols: int
    rows: int
    number: int
    title: str


def parse_ls(text: str) -> list[Session]:
    sessions = []
    for line in text.splitlines():
        m = _LS_RE.match(line)
        if m:
            sessions.append(Session(int(m["pid"]), m["name"], m["date"] or "", m["state"].strip()))
    return sessions


def parse_info(text: str) -> WindowInfo | None:
    m = _INFO_RE.search(text)
    if not m:
        return None
    w = _INFO_WIN_RE.search(text.strip())
    return WindowInfo(
        cols=int(m[3]),
        rows=int(m[4]),
        number=int(w[1]) if w else 0,
        title=w[2] if w else "",
    )


_DATE_FORMATS = ("%m/%d/%y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d/%m/%y %H:%M:%S", "%Y-%m-%d %H:%M:%S")


def parse_date(text: str) -> float:
    """screen -ls 의 날짜를 정렬용 타임스탬프로. 실패하면 0."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def list_sessions(screen_bin: str) -> list[Session]:
    # screen -ls 는 세션이 있어도 종료코드 1을 돌려주므로 코드는 보지 않는다.
    out = subprocess.run([screen_bin, "-ls"], capture_output=True, text=True, errors="replace")
    return parse_ls(out.stdout)


def window_info(screen_bin: str, session_id: str, attempts: int = 2) -> WindowInfo | None:
    for _ in range(attempts):
        try:
            out = subprocess.run(
                [screen_bin, "-S", session_id, "-Q", "info"],
                capture_output=True, text=True, errors="replace", timeout=2,
            )
        except subprocess.TimeoutExpired:
            continue
        info = parse_info(out.stdout)
        if info:
            return info
    log.warning("screen -Q info 실패, 이번에는 화면 캡처를 건너뜀: %s", session_id)
    return None


def attach_args(screen_bin: str, session: Session, attached_mode: str) -> list[str]:
    if session.attached:
        flag = ["-x"] if attached_mode == "share" else ["-d", "-r"]
    else:
        flag = ["-r"]
    return [screen_bin, *flag, session.id]

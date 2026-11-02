"""세션 하나를 화면에 보여 줄 정보(SessionView)로 정리한다."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import capture, procinfo, screens, titles
from .config import Config

log = logging.getLogger(__name__)

SHELLS = {"bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh", "csh"}


@dataclass
class SessionView:
    session: screens.Session
    kind: str = "unknown"        # claude | codex | shell | program | unknown
    title: str = ""              # 타일 제목
    detail: str = ""             # 보조 정보 (프로그램 이름, 경로 등)
    cwd: str = ""
    cols: int = 80
    rows: int = 24
    window: int = 0
    size_known: bool = False     # 창 크기를 모르면 캡처하지 않는다 (다른 크기로 붙으면 창이 리사이즈됨)
    lines: list[str] = field(default_factory=list)
    captured: bool = False


def short_path(path: str, home: str | None = None) -> str:
    home = home if home is not None else os.path.expanduser("~")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


def describe(cfg: Config, session: screens.Session, procs: dict[int, procinfo.Proc]) -> SessionView:
    view = SessionView(session=session)
    if session.dead:
        view.kind, view.title = "unknown", "(죽은 세션 · screen -wipe 필요)"
        return view

    info = screens.window_info(cfg.screen_bin, session.id)
    if info:
        view.cols, view.rows, view.window = info.cols, info.rows, info.number
        view.size_known = True

    shell = procinfo.window_shell(procs, session.pid, view.window)
    if shell is None:
        view.title = session.name
        return view

    fg = procinfo.foreground(procs, shell)
    tree = procinfo.descendants(procs, fg.pid)

    claude = next((p for p in tree if procinfo.is_claude(p)), None)
    if claude:
        title, cwd = titles.claude_title(cfg.claude_dir, claude.pid)
        view.kind = "claude"
        view.cwd = cwd or procinfo.cwd_of(claude.pid) or ""
        view.title = title or "claude"
        view.detail = f"claude · {short_path(view.cwd)}"
        return view

    codex = next((p for p in tree if procinfo.is_codex(p)), None)
    if codex:
        view.kind = "codex"
        view.cwd = procinfo.cwd_of(codex.pid) or ""
        view.title = titles.codex_title(cfg.codex_dir, procs, codex.pid, view.cwd) or "codex (새 대화)"
        view.detail = f"codex · {short_path(view.cwd)}"
        return view

    # 일반 셸: 셸의 pwd 를 제목으로
    view.cwd = procinfo.cwd_of(shell.pid) or ""
    view.title = short_path(view.cwd) or session.name
    if fg.pid == shell.pid or fg.exe_name.lstrip("-") in SHELLS:
        view.kind = "shell"
        view.detail = shell.exe_name.lstrip("-")
    else:
        view.kind = "program"
        view.detail = " ".join(fg.argv[:3]) or fg.comm
    return view


def collect(cfg: Config) -> list[SessionView]:
    """세션 목록 + 메타데이터 (화면 캡처는 하지 않음)."""
    sessions = screens.list_sessions(cfg.screen_bin)
    procs = procinfo.snapshot()
    views = []
    for s in sessions:
        try:
            views.append(describe(cfg, s, procs))
        except Exception:  # 세션 하나 때문에 전체 목록이 죽지 않게
            log.exception("세션 정보 수집 실패: %s", s.id)
            views.append(SessionView(session=s, title=s.name))
    # 최근에 만든 세션이 앞에 오도록. 날짜를 못 읽으면 뒤로.
    views.sort(key=lambda v: (screens.parse_date(v.session.date), v.session.pid), reverse=True)
    return views


def capture_views(cfg: Config, views: list[SessionView]) -> None:
    targets = [v for v in views if not v.session.dead and v.size_known]
    if not targets or cfg.capture_mode == "off":
        return

    def one(v: SessionView) -> None:
        try:
            v.lines = capture.capture(cfg.capture_mode, cfg.screen_bin, v.session.id,
                                      v.cols, v.rows, v.window, cfg.capture_timeout)
            v.captured = True
        except Exception:
            log.exception("화면 캡처 실패: %s", v.session.id)

    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        list(pool.map(one, targets))

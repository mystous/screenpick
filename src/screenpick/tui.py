"""curses 그리드 화면."""

from __future__ import annotations

import curses
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass

from . import __version__, model, screens
from .config import Config
from .textwidth import crop, pad, truncate, width

log = logging.getLogger(__name__)

KIND_LABEL = {"claude": "claude", "codex": "codex", "shell": "shell", "program": "run", "unknown": "?"}
KIND_COLOR = {"claude": 1, "codex": 2, "shell": 3, "program": 4, "unknown": 5}
HEADER_LINES = 1
FOOTER_LINES = 1
GAP = 1


# ------------------------------------------------------------------ 배경 갱신

class Refresher:
    """세션 정보 수집과 화면 캡처를 백그라운드에서 돌린다 (UI 가 멈추지 않게)."""

    def __init__(self, cfg: Config, visible_ids):
        self.cfg = cfg
        self.visible_ids = visible_ids  # callable(views) -> 지금 화면에 보이는 세션 id 들
        self.lock = threading.Lock()
        self.views: list[model.SessionView] = []
        self.thumbs: dict[str, list[str]] = {}
        self.loaded = False
        self.busy = False
        self.updated_at = 0.0
        self.version = 0
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="refresher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request(self) -> None:
        self._wake.set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()
        self._wake.set()

    def _bump(self) -> None:
        self.version += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._paused.is_set():
                self._cycle()
            timeout = self.cfg.refresh_sec if self.cfg.refresh_sec > 0 else None
            self._wake.wait(timeout)
            self._wake.clear()

    def _cycle(self) -> None:
        self.busy = True
        try:
            views = model.collect(self.cfg)
            with self.lock:
                self.views, self.loaded = views, True
                self._bump()
            wanted = set(self.visible_ids(views))
            targets = [v for v in views if v.session.id in wanted]
            if self._paused.is_set():
                return
            model.capture_views(self.cfg, targets)
            with self.lock:
                for v in targets:
                    if v.captured:
                        self.thumbs[v.session.id] = v.lines
                alive = {v.session.id for v in self.views}
                self.thumbs = {k: val for k, val in self.thumbs.items() if k in alive}
                self.updated_at = time.time()
                self._bump()
        except Exception:
            log.exception("갱신 실패")
        finally:
            self.busy = False


# ------------------------------------------------------------------ 레이아웃

@dataclass(frozen=True)
class Layout:
    cols: int
    rows: int
    tile_w: int
    tile_h: int

    @property
    def per_page(self) -> int:
        return self.cols * self.rows


def compute_layout(term_w: int, term_h: int, min_tile_w: int, tile_h: int) -> Layout:
    area_h = max(1, term_h - HEADER_LINES - FOOTER_LINES)
    cols = max(1, (term_w + GAP) // (min_tile_w + GAP))
    tile_w = max(10, (term_w - GAP * (cols - 1)) // cols)
    tile_h = min(tile_h, area_h)
    rows = max(1, (area_h + GAP) // (tile_h + GAP)) if tile_h > 0 else 1
    # 세로로 남는 공간이 크면 타일을 조금 키운다
    tile_h = max(tile_h, min((area_h - GAP * (rows - 1)) // rows, tile_h + 4))
    return Layout(cols, rows, tile_w, max(3, tile_h))


def thumbnail(lines: list[str], inner_w: int, inner_h: int) -> list[str]:
    """화면의 가장 최근(아래쪽) 내용이 보이도록 마지막 줄들을 잘라 온다."""
    content = list(lines)
    while content and not content[-1].strip():
        content.pop()
    tail = content[-inner_h:] if inner_h > 0 else []
    return [crop(l, inner_w) for l in tail]


# ------------------------------------------------------------------ 앱

class App:
    def __init__(self, stdscr, cfg: Config):
        self.scr = stdscr
        self.cfg = cfg
        self.sel = 0
        self.layout = Layout(1, 1, cfg.tile_width, cfg.tile_height)
        self.message = ""
        self.message_until = 0.0
        self.show_help = False
        self.tile_boxes: list[tuple[int, int, int, int, int]] = []  # (y, x, h, w, index)
        self.my_sty = os.environ.get("STY", "")
        self.refresher = Refresher(cfg, self._visible_ids)
        self.attach_request: list[str] | None = None
        self.attach_env: dict[str, str] | None = None

    # ---- 상태
    def _visible_ids(self, views) -> list[str]:
        per = self.layout.per_page
        start = (min(self.sel, max(0, len(views) - 1)) // per) * per
        return [v.session.id for v in views[start:start + per]]

    def flash(self, msg: str, sec: float = 3.0) -> None:
        self.message, self.message_until = msg, time.time() + sec

    # ---- 색
    def init_colors(self) -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        orange = 208 if curses.COLORS >= 256 else curses.COLOR_YELLOW
        curses.init_pair(1, orange, bg)
        curses.init_pair(2, curses.COLOR_CYAN, bg)
        curses.init_pair(3, curses.COLOR_GREEN, bg)
        curses.init_pair(4, curses.COLOR_MAGENTA, bg)
        curses.init_pair(5, curses.COLOR_WHITE, bg)
        # 머리줄: 어두운 배경 + 밝은 글씨 (bold 가 글씨를 밝게 바꾸는 터미널에서도 대비 유지)
        if curses.COLORS >= 256:
            curses.init_pair(6, 231, 24)  # 흰색 on 짙은 남색
        else:
            curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLUE)

    def color(self, n: int) -> int:
        return curses.color_pair(n) if curses.has_colors() else 0

    # ---- 그리기 도우미
    def put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        text = crop(text, w - x - (1 if y == h - 1 else 0))  # 오른쪽 아래 칸에 쓰면 curses 오류
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def draw_tile(self, y: int, x: int, view: model.SessionView, index_on_page: int, selected: bool) -> None:
        L = self.layout
        w, h = L.tile_w, L.tile_h
        inner_w = w - 4
        kind_attr = self.color(KIND_COLOR.get(view.kind, 5))
        border = (kind_attr | curses.A_BOLD) if selected else curses.A_DIM
        is_me = view.session.id == self.my_sty

        # 윗 테두리: ┌ 1 제목 ────── claude ┐
        label = f" {KIND_LABEL.get(view.kind, '?')} "
        num = f" {index_on_page + 1} " if index_on_page < 9 else " "
        title_room = w - 2 - width(num) - width(label) - 2
        title = " " + truncate(view.title or view.session.name, max(1, title_room)) + " "
        fill = w - 2 - width(num) - width(title) - width(label)
        self.put(y, x, "┏" if selected else "┌", border)
        self.put(y, x + 1, num, border)
        title_attr = (curses.A_REVERSE | curses.A_BOLD | kind_attr) if selected else (curses.A_BOLD | kind_attr)
        self.put(y, x + 1 + width(num), title, title_attr)
        bar = "━" if selected else "─"
        self.put(y, x + 1 + width(num) + width(title), bar * max(0, fill), border)
        self.put(y, x + w - 1 - width(label), label, kind_attr)
        self.put(y, x + w - 1, "┓" if selected else "┐", border)

        side = "┃" if selected else "│"
        # 둘째 줄: 보조 정보
        body = [truncate(view.detail, inner_w)] if view.detail else []
        thumbs = self.refresher.thumbs.get(view.session.id)
        room = h - 2 - len(body)
        if thumbs is None:
            pics = ["(화면 불러오는 중…)"] if not view.session.dead else []
        else:
            pics = thumbnail(thumbs, inner_w, room)
        for row in range(h - 2):
            yy = y + 1 + row
            self.put(yy, x, side, border)
            self.put(yy, x + 1, " " * (w - 2))
            if row < len(body):
                self.put(yy, x + 2, body[row], curses.A_DIM | kind_attr)
            else:
                i = row - len(body)
                # 썸네일은 아래쪽에 붙인다 (터미널처럼)
                offset = room - len(pics)
                if 0 <= i - offset < len(pics):
                    self.put(yy, x + 2, pics[i - offset])
            self.put(yy, x + w - 1, side, border)

        # 아랫 테두리: └ 세션이름 · 상태 ─────┘
        state = "현재 세션" if is_me else view.session.state
        foot = " " + truncate(f"{view.session.id} · {state}", w - 6) + " "
        self.put(y + h - 1, x, "┗" if selected else "└", border)
        self.put(y + h - 1, x + 1, foot, (curses.A_BOLD if view.session.attached else curses.A_DIM))
        self.put(y + h - 1, x + 1 + width(foot), bar * max(0, w - 2 - width(foot)), border)
        self.put(y + h - 1, x + w - 1, "┛" if selected else "┘", border)

    def draw(self) -> None:
        self.scr.erase()
        H, W = self.scr.getmaxyx()
        self.layout = compute_layout(W, H, self.cfg.tile_width, self.cfg.tile_height)
        L = self.layout
        with self.refresher.lock:
            views = list(self.refresher.views)
        n = len(views)
        if n:
            self.sel = max(0, min(self.sel, n - 1))
        per = L.per_page
        page = self.sel // per
        pages = max(1, (n + per - 1) // per)

        # 머리줄
        status = "갱신 중…" if self.refresher.busy else (
            time.strftime("갱신 %H:%M:%S", time.localtime(self.refresher.updated_at))
            if self.refresher.updated_at else "")
        head = f" screenpick  ·  세션 {n}개  ·  페이지 {page + 1}/{pages}  ·  {status}"
        if self.my_sty:
            head += "  ·  ⚠ screen 안에서 실행 중"
        self.put(0, 0, pad(head, W), self.color(6) | curses.A_BOLD)
        ver = f"v{__version__} "
        if width(head) + width(ver) + 2 <= W:
            self.put(0, W - width(ver), ver, self.color(6) | curses.A_BOLD)

        self.tile_boxes = []
        if not self.refresher.loaded:
            self.put(H // 2, max(0, W // 2 - 10), "세션 목록 불러오는 중…")
        elif not views:
            self.put(H // 2, max(0, W // 2 - 15), "실행 중인 screen 세션이 없습니다")
            self.put(H // 2 + 1, max(0, W // 2 - 15), "c 를 눌러 새 세션을 만드세요", curses.A_DIM)
        else:
            start = page * per
            for i, view in enumerate(views[start:start + per]):
                r, c = divmod(i, L.cols)
                y = HEADER_LINES + r * (L.tile_h + GAP)
                x = c * (L.tile_w + GAP)
                self.draw_tile(y, x, view, i, start + i == self.sel)
                self.tile_boxes.append((y, x, L.tile_h, L.tile_w, start + i))

        if self.show_help:
            self.draw_help(H, W)

        # 꼬리줄
        if self.message and time.time() < self.message_until:
            foot = " " + self.message
        else:
            self.message = ""
            foot = " ←↑↓→/hjkl 이동  Enter 붙기  1-9 바로 붙기  c 새 세션  n/p 페이지  r 새로고침  ? 도움말  q 종료"
        self.put(H - 1, 0, pad(foot, W), curses.A_REVERSE)
        self.scr.noutrefresh()
        curses.doupdate()

    def draw_help(self, H: int, W: int) -> None:
        lines = [
            "screenpick 도움말",
            "",
            "←↑↓→ / h j k l   타일 이동",
            "Enter / Space    선택한 세션에 붙기",
            "1 ~ 9            현재 페이지의 n번째 세션에 바로 붙기",
            "n PgDn ]         다음 페이지",
            "p PgUp [         이전 페이지",
            "g Home / G End   처음 / 마지막 세션",
            "c                새 screen 세션 만들기 (이름 입력)",
            "r                지금 새로고침",
            "마우스           클릭 선택, 더블클릭 붙기",
            "q Esc            종료",
            "",
            "세션에서 Ctrl-a d 로 떼어 내면 이 화면으로 돌아옵니다.",
        ]
        bw = min(W - 2, max(width(l) for l in lines) + 4)
        bh = min(H - 2, len(lines) + 2)
        y0, x0 = max(0, (H - bh) // 2), max(0, (W - bw) // 2)
        for i in range(bh):
            if i == 0 or i == bh - 1:
                edge = ("┌" if i == 0 else "└") + "─" * (bw - 2) + ("┐" if i == 0 else "┘")
                self.put(y0 + i, x0, edge, curses.A_BOLD)
            else:
                text = lines[i - 1] if i - 1 < len(lines) else ""
                self.put(y0 + i, x0, "│ " + pad(truncate(text, bw - 4), bw - 4) + " │", curses.A_BOLD if i == 1 else 0)

    # ---- 동작
    def current_views(self) -> list[model.SessionView]:
        with self.refresher.lock:
            return list(self.refresher.views)

    def move(self, dx: int, dy: int) -> None:
        n = len(self.current_views())
        if not n:
            return
        L = self.layout
        per = L.per_page
        page_start = (self.sel // per) * per
        i = self.sel - page_start
        r, c = divmod(i, L.cols)
        if dx:
            nc = c + dx
            if nc < 0:  # 왼쪽 끝 → 이전 페이지 같은 줄 오른쪽 끝
                target = self.sel - 1
            elif nc >= L.cols:
                target = self.sel + 1
            else:
                target = page_start + r * L.cols + nc
        else:
            target = self.sel + dy * L.cols
        if 0 <= target < n:
            self.change_sel(target)

    def change_sel(self, target: int) -> None:
        per = self.layout.per_page
        old_page = self.sel // per
        self.sel = target
        if target // per != old_page:
            self.refresher.request()  # 새 페이지 썸네일 캡처

    def page(self, delta: int) -> None:
        n = len(self.current_views())
        if not n:
            return
        per = self.layout.per_page
        pages = (n + per - 1) // per
        p = self.sel // per + delta
        if 0 <= p < pages:
            self.change_sel(min(n - 1, p * per + (self.sel % per)))

    def attach(self, index: int) -> bool:
        """True 를 돌려주면 앱을 끝낸다."""
        views = self.current_views()
        if not (0 <= index < len(views)):
            return False
        view = views[index]
        if view.session.dead:
            self.flash("죽은 세션입니다. 'screen -wipe' 로 정리하세요.")
            return False
        if view.session.id == self.my_sty:
            self.flash("지금 screenpick 이 돌고 있는 바로 그 세션입니다.")
            return False
        args = screens.attach_args(self.cfg.screen_bin, view.session, self.cfg.attached_mode)
        return self.run_screen(args, f"{view.session.id} 에서 돌아왔습니다.")

    def run_screen(self, args: list[str], back_message: str, env: dict[str, str] | None = None) -> bool:
        """screen 을 전경에서 실행하고, detach 하면 그리드로 돌아온다. True 면 앱 종료."""
        if not self.cfg.return_after_detach:
            self.attach_request = args
            self.attach_env = env
            return True
        self.refresher.pause()
        curses.def_prog_mode()
        curses.endwin()
        try:
            subprocess.run(args, env=env)
        finally:
            curses.reset_prog_mode()
            self.scr.clear()
            self.refresher.resume()
        self.flash(back_message)
        return False

    def new_session(self) -> bool:
        name = self.prompt("새 세션 이름 (비우면 자동 · Enter 만들기 · Esc 취소): ")
        if name is None:
            return False
        name = name.strip()
        if name:
            problem = screens.check_session_name(name)
            if problem:
                self.flash(problem)
                return False
            if any(v.session.name == name for v in self.current_views()):
                self.flash(f"'{name}' 이름의 세션이 이미 있습니다.")
                return False
        args = screens.new_session_args(self.cfg.screen_bin, name)
        # screen 안에서 STY 가 남아 있으면 새 세션 대신 현재 세션에 창이 하나 생긴다
        env = {k: v for k, v in os.environ.items() if k not in ("STY", "WINDOW")}
        done = self.run_screen(args, f"새 세션{' ' + name if name else ''} 에서 돌아왔습니다.", env)
        self.sel = 0  # 새 세션은 가장 최근이라 맨 앞에 온다
        return done

    def prompt(self, label: str) -> str | None:
        """꼬리줄에서 한 줄 입력받기. Esc 면 None. 한글 입력을 위해 get_wch 를 쓴다."""
        buf: list[str] = []
        self.scr.timeout(-1)
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                H, W = self.scr.getmaxyx()
                text = label + "".join(buf)
                shown = text if width(text) < W - 1 else "…" + text[-(W // 2):]
                self.put(H - 1, 0, pad(" " + shown, W), curses.A_REVERSE)
                try:
                    self.scr.move(H - 1, min(W - 2, 1 + width(shown)))
                except curses.error:
                    pass
                self.scr.refresh()
                try:
                    ch = self.scr.get_wch()
                except curses.error:
                    continue
                if ch in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(buf)
                if ch == "\x1b":
                    return None
                if ch in ("\x7f", "\b", curses.KEY_BACKSPACE):
                    if buf:
                        buf.pop()
                elif ch == "\x15":  # Ctrl-U: 전부 지우기
                    buf.clear()
                elif isinstance(ch, str) and ch.isprintable():
                    buf.append(ch)
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.scr.timeout(150)

    def on_mouse(self) -> bool:
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return False
        for y, x, h, w, idx in self.tile_boxes:
            if y <= my < y + h and x <= mx < x + w:
                self.sel = idx
                if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                    return self.attach(idx)
        return False

    def handle(self, key: int) -> bool:
        if self.show_help and key != -1:
            self.show_help = False
            return False
        if key in (ord("q"), 27):
            return True
        if key in (curses.KEY_LEFT, ord("h")):
            self.move(-1, 0)
        elif key in (curses.KEY_RIGHT, ord("l")):
            self.move(1, 0)
        elif key in (curses.KEY_UP, ord("k")):
            self.move(0, -1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move(0, 1)
        elif key in (curses.KEY_NPAGE, ord("n"), ord("]")):
            self.page(1)
        elif key in (curses.KEY_PPAGE, ord("p"), ord("[")):
            self.page(-1)
        elif key in (curses.KEY_HOME, ord("g")):
            self.change_sel(0)
        elif key in (curses.KEY_END, ord("G")):
            self.change_sel(max(0, len(self.current_views()) - 1))
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return self.attach(self.sel)
        elif ord("1") <= key <= ord("9"):
            per = self.layout.per_page
            idx = (self.sel // per) * per + (key - ord("1"))
            if idx < len(self.current_views()) and key - ord("1") < per:
                self.sel = idx
                return self.attach(idx)
        elif key == ord("c"):
            return self.new_session()
        elif key == ord("r"):
            self.flash("새로고침…", 1.0)
            self.refresher.request()
        elif key == ord("?"):
            self.show_help = True
        elif key == curses.KEY_MOUSE:
            return self.on_mouse()
        elif key == curses.KEY_RESIZE:
            self.refresher.request()  # 한 페이지에 들어가는 개수가 바뀔 수 있음
        return False

    def run(self) -> tuple[list[str] | None, dict[str, str] | None]:
        curses.curs_set(0)
        self.scr.keypad(True)
        curses.set_escdelay(25)
        curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED)
        self.init_colors()
        H, W = self.scr.getmaxyx()
        self.layout = compute_layout(W, H, self.cfg.tile_width, self.cfg.tile_height)
        self.refresher.start()
        self.scr.timeout(150)
        seen = -1
        try:
            while True:
                if seen != self.refresher.version or self.message or self.refresher.busy:
                    seen = self.refresher.version
                    self.draw()
                key = self.scr.getch()
                if key == -1:
                    continue
                if self.handle(key):
                    break
                self.draw()
        finally:
            self.refresher.stop()
        return self.attach_request, self.attach_env


def run(cfg: Config) -> tuple[list[str] | None, dict[str, str] | None]:
    """앱을 실행하고, 끝날 때 exec 할 screen 명령과 환경(있다면)을 돌려준다."""
    return curses.wrapper(lambda stdscr: App(stdscr, cfg).run())

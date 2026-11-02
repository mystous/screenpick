"""명령줄 진입점."""

from __future__ import annotations

import argparse
import locale
import logging
import os
import shutil
import sys

from . import __version__, config, model
from .textwidth import pad, truncate


class _BufferHandler(logging.Handler):
    """curses 가 화면을 쓰는 동안 로그를 모아 두었다가 끝난 뒤 stderr 로 흘려보낸다.

    (Twelve-Factor XI. Logs: 로그는 파일이 아니라 표준 스트림으로)
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))

    def flush_to(self, stream) -> None:
        for line in self.records:
            print(line, file=stream)
        self.records.clear()


def _print_list(cfg: config.Config, with_screen: bool) -> int:
    views = model.collect(cfg)
    if with_screen:
        model.capture_views(cfg, views)
    if not views:
        print("실행 중인 screen 세션이 없습니다")
        return 0
    term_w = shutil.get_terminal_size((120, 24)).columns
    for v in views:
        kind = pad(v.kind, 8)
        sid = pad(truncate(v.session.id, 32), 32)
        state = pad(v.session.state, 10)
        title = truncate(v.title, max(10, term_w - 56))
        print(f"{kind}{sid} {state} {title}")
        if with_screen and v.lines:
            content = [l for l in v.lines if l.strip()][-5:]
            for l in content:
                print("    │ " + truncate(l, term_w - 8))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="screenpick",
        description="GNU screen 세션을 썸네일 그리드로 보고 골라서 붙습니다. 설정은 SCREENPICK_* 환경변수로 합니다.",
    )
    parser.add_argument("-l", "--list", action="store_true", help="화면 없이 세션 목록만 출력")
    parser.add_argument("--peek", action="store_true", help="--list 와 함께: 각 세션 화면의 마지막 몇 줄도 출력")
    parser.add_argument("-V", "--version", action="version", version=f"screenpick {__version__}")
    args = parser.parse_args(argv)

    locale.setlocale(locale.LC_ALL, "")
    try:
        cfg = config.load()
    except ValueError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.list:
        logging.basicConfig(level=cfg.log_level, format=fmt._fmt, stream=sys.stderr)
        if shutil.which(cfg.screen_bin) is None:
            print(f"screen 실행 파일을 찾을 수 없습니다: {cfg.screen_bin}", file=sys.stderr)
            return 1
        return _print_list(cfg, args.peek)

    if not sys.stdout.isatty():
        print("터미널에서 실행해 주세요 (목록만 보려면 --list)", file=sys.stderr)
        return 1
    if shutil.which(cfg.screen_bin) is None:
        print(f"screen 실행 파일을 찾을 수 없습니다: {cfg.screen_bin}", file=sys.stderr)
        return 1

    buffer = _BufferHandler()
    buffer.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    root.addHandler(buffer)

    from . import tui  # curses 는 대화형 모드에서만 필요
    try:
        exec_args, exec_env = tui.run(cfg)
    except KeyboardInterrupt:
        exec_args, exec_env = None, None
    finally:
        buffer.flush_to(sys.stderr)

    if exec_args:
        if exec_env is not None:
            os.execvpe(exec_args[0], exec_args, exec_env)
        os.execvp(exec_args[0], exec_args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

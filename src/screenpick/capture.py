"""세션 화면 캡처.

screen 4.x 의 `hardcopy` 는 detach 된 세션에서 UTF-8 문자의 상위 바이트를 버려
한글·박스 문자가 복원 불가능하게 깨진다. 그래서 기본 방식(pty)은
창과 똑같은 크기의 가상 터미널로 `screen -x` 잠깐 붙어서, screen 이 다시 그려 주는
화면을 pyte(터미널 에뮬레이터)로 렌더링한 뒤 바로 떨어진다.
크기가 같으므로 창 크기 변경(SIGWINCH)이 일어나지 않는다.
"""

from __future__ import annotations

import fcntl
import logging
import os
import pty
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
from pathlib import Path

log = logging.getLogger(__name__)

# 출력이 이 시간 동안 멈추면 화면을 다 받은 것으로 본다.
_QUIET_SEC = 0.2
_MIN_WAIT_SEC = 0.15


def _utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env["TERM"] = "xterm"
    ctype = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
    if "utf" not in ctype.lower().replace("-", ""):
        env["LC_ALL"] = "C.UTF-8"
    return env


def capture_pty(screen_bin: str, session_id: str, cols: int, rows: int, timeout: float) -> list[str]:
    import pyte  # 선택 의존성처럼 다루기 위해 지연 import

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    # setsid -c: 새 세션을 만들고 pty 를 제어 터미널로 잡는다 (스레드에서 fork 하지 않기 위함).
    argv = [screen_bin, "-x", session_id]
    if shutil.which("setsid"):
        argv = ["setsid", "-c", *argv]
    proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, close_fds=True, env=_utf8_env())
    os.close(slave)

    screen = pyte.Screen(cols, rows)
    stream = pyte.ByteStream(screen)
    start = last = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if now - start > timeout:
                break
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                stream.feed(data)
                last = time.monotonic()
            elif now - last > _QUIET_SEC and now - start > _MIN_WAIT_SEC and last > start:
                break
    finally:
        os.close(master)  # pty 가 닫히면 screen 은 이 display 만 떼어 낸다
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return render(screen)


def render(screen) -> list[str]:
    """pyte 화면을 줄 목록으로.

    pyte 의 Screen.display 는 전각 문자의 절반이 덮어쓰인 칸에서 IndexError 를 내므로
    버퍼를 직접 읽는다. 전각 문자 뒤의 빈 자리표시 칸("")은 건너뛴다.
    """
    lines = []
    for y in range(screen.lines):
        row = screen.buffer[y]
        lines.append("".join(row[x].data for x in range(screen.columns)).rstrip())
    return lines


def capture_hardcopy(screen_bin: str, session_id: str, window: int, timeout: float) -> list[str]:
    """대체 방식. ASCII 외 문자는 깨지므로 '·' 로 바꾼다."""
    with tempfile.TemporaryDirectory(prefix="screenpick-") as tmp:
        path = Path(tmp) / "hardcopy.txt"
        subprocess.run(
            [screen_bin, "-S", session_id, "-p", str(window), "-X", "hardcopy", str(path)],
            capture_output=True, timeout=timeout,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not (path.exists() and path.stat().st_size):
            time.sleep(0.03)
        if not path.exists():
            return []
        raw = path.read_bytes()
    text = "".join(chr(b) if b < 0x80 else "·" for b in raw)
    return [line.rstrip() for line in text.splitlines()]


def capture(mode: str, screen_bin: str, session_id: str, cols: int, rows: int, window: int,
            timeout: float) -> list[str]:
    if mode == "off":
        return []
    if mode == "pty":
        try:
            return capture_pty(screen_bin, session_id, cols, rows, timeout)
        except ImportError:
            log.warning("pyte 가 없어 hardcopy 방식으로 대체합니다 (한글이 깨질 수 있음)")
        except OSError as exc:
            log.warning("pty 캡처 실패(%s): %s", session_id, exc)
    return capture_hardcopy(screen_bin, session_id, window, timeout)

"""screenpick 을 한 번에 하나만 돌게 한다.

잊고 남겨 둔 screenpick 이 계속 screen 을 조회하면 다른 터미널의 입력을 방해한다.
그래서 새로 켤 때 같은 사용자의 이전 screenpick 을 찾아 종료시킨다.
잠금 파일 대신 /proc 를 보는 이유: 잠금을 모르는 옛 버전도 찾아야 하기 때문이다.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

# 화면 없이 잠깐 돌고 끝나는 실행은 건드리지 않는다.
_ONESHOT_FLAGS = {"-l", "--list", "--peek", "-V", "--version", "-h", "--help"}


def is_screenpick(argv: list[str]) -> bool:
    """명령줄이 대화형 screenpick 인지. `screenpick`, `python .../bin/screenpick`, `python -m screenpick`."""
    if not argv:
        return False
    names = [os.path.basename(a) for a in argv[:2]]
    if names[0] == "screenpick":
        rest = argv[1:]
    elif len(argv) >= 2 and names[0].startswith("python") and names[1] == "screenpick":
        rest = argv[2:]
    elif len(argv) >= 3 and names[0].startswith("python") and argv[1] == "-m" and argv[2] == "screenpick":
        rest = argv[3:]
    else:
        return False
    return not any(a in _ONESHOT_FLAGS for a in rest)


def find_others(proc_root: Path = Path("/proc"), me: int | None = None, uid: int | None = None) -> list[int]:
    """같은 사용자가 돌리고 있는 다른 screenpick 의 PID 들."""
    me = os.getpid() if me is None else me
    uid = os.getuid() if uid is None else uid
    found = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            argv = [a.decode(errors="replace") for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if is_screenpick(argv):
            found.append(int(entry.name))
    return sorted(found)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # 부모가 거두지 않은 진짜 좀비(Z)는 이미 끝난 것으로 본다.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def screen_children(pid: int, proc_root: Path = Path("/proc")) -> list[int]:
    """pid 가 띄운 screen 클라이언트들 (screenpick 안에서 세션에 들어가 있는 경우)."""
    out = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            comm = stat[stat.index("(") + 1:stat.rindex(")")]
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if ppid == pid and comm == "screen":
            out.append(int(entry.name))
    return out


def terminate(pids: list[int], wait_sec: float = 2.0) -> list[int]:
    """SIGTERM 을 보내고 기다렸다가, 남아 있으면 SIGKILL. 종료시킨 PID 들을 돌려준다.

    연결이 끊겼는데 서버가 모르고 있으면 이전 screenpick 밑의 `screen -r` 이
    죽은 터미널에 세션을 붙잡고 있다. screen 클라이언트는 SIGHUP 을 받으면
    세션을 깔끔하게 detach 하므로 그것부터 보낸다.
    """
    for pid in pids:
        for child in screen_children(pid):
            try:
                os.kill(child, signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass
    done = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            done.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline and any(_alive(p) for p in done):
        time.sleep(0.05)
    for pid in done:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return done


def take_over() -> list[int]:
    """이전 screenpick 들을 끝내고 그 PID 들을 돌려준다."""
    return terminate(find_others())

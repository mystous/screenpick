"""/proc 를 읽어 screen 창 안에서 지금 무엇이 돌고 있는지 알아낸다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Proc:
    pid: int
    ppid: int
    comm: str
    tpgid: int
    argv: list[str]
    children: list[int] = field(default_factory=list)

    @property
    def exe_name(self) -> str:
        return os.path.basename(self.argv[0]) if self.argv else self.comm


def parse_stat(text: str) -> tuple[str, int, int]:
    """/proc/<pid>/stat 에서 (comm, ppid, tpgid). comm 에 공백·괄호가 있을 수 있다."""
    lpar, rpar = text.index("("), text.rindex(")")
    comm = text[lpar + 1:rpar]
    rest = text[rpar + 2:].split()
    # rest[0]=state, [1]=ppid, [2]=pgrp, [3]=session, [4]=tty_nr, [5]=tpgid
    return comm, int(rest[1]), int(rest[5])


def snapshot(proc_root: Path = Path("/proc")) -> dict[int, Proc]:
    procs: dict[int, Proc] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm, ppid, tpgid = parse_stat((entry / "stat").read_text(errors="replace"))
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except (OSError, ValueError, IndexError):
            continue
        pid = int(entry.name)
        procs[pid] = Proc(pid, ppid, comm, tpgid, [a.decode(errors="replace") for a in argv if a])
    for p in procs.values():
        if p.ppid in procs:
            procs[p.ppid].children.append(p.pid)
    return procs


def start_time(pid: int, proc_root: Path = Path("/proc")) -> float | None:
    """프로세스 시작 시각(유닉스 초)."""
    try:
        stat = (proc_root / str(pid) / "stat").read_text()
        rest = stat[stat.rindex(")") + 2:].split()
        ticks = int(rest[19])  # 22번째 필드 starttime
        btime = next(int(l.split()[1]) for l in (proc_root / "stat").read_text().splitlines()
                     if l.startswith("btime "))
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    return btime + ticks / os.sysconf("SC_CLK_TCK")


def read_environ_var(pid: int, name: str) -> str | None:
    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    prefix = name.encode() + b"="
    for item in data.split(b"\0"):
        if item.startswith(prefix):
            return item[len(prefix):].decode(errors="replace")
    return None


def cwd_of(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def open_files(pid: int) -> list[str]:
    fd_dir = f"/proc/{pid}/fd"
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return []
    paths = []
    for n in names:
        try:
            paths.append(os.readlink(os.path.join(fd_dir, n)))
        except OSError:
            pass
    return paths


def descendants(procs: dict[int, Proc], pid: int) -> list[Proc]:
    """pid 자신을 포함해 너비 우선으로 자손을 돌려준다."""
    out, queue, seen = [], [pid], set()
    while queue:
        cur = queue.pop(0)
        if cur in seen or cur not in procs:
            continue
        seen.add(cur)
        out.append(procs[cur])
        queue.extend(sorted(procs[cur].children))
    return out


def window_shell(procs: dict[int, Proc], screen_pid: int, window: int) -> Proc | None:
    """screen 서버 프로세스의 자식 중 해당 창(WINDOW=n)의 첫 프로세스."""
    kids = [procs[c] for c in sorted(procs[screen_pid].children)] if screen_pid in procs else []
    for k in kids:
        if read_environ_var(k.pid, "WINDOW") == str(window):
            return k
    return kids[0] if kids else None


def foreground(procs: dict[int, Proc], shell: Proc) -> Proc:
    """터미널의 전경 프로세스 그룹 리더. 셸이 놀고 있으면 셸 자신."""
    if shell.tpgid > 0 and shell.tpgid in procs:
        return procs[shell.tpgid]
    return shell


def is_claude(p: Proc) -> bool:
    return p.comm == "claude" or p.exe_name == "claude"


def is_codex(p: Proc) -> bool:
    if p.comm == "codex" or p.exe_name == "codex":
        return True
    # npm 으로 깐 codex 는 "node .../bin/codex" 형태
    return len(p.argv) >= 2 and os.path.basename(p.argv[0]) in ("node", "nodejs") \
        and os.path.basename(p.argv[1]) == "codex"

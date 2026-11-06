import json
import sqlite3
import time
from pathlib import Path

import pytest

from screenpick import config, procinfo, screens, titles, tui
from screenpick.textwidth import crop, truncate, width

LS_OUTPUT = """There are screens on:
\t12345.pts-0.myhost\t(11/02/26 19:16:07)\t(Detached)
\t460568.work\t(11/01/26 12:13:53)\t(Attached)
\t123.old\t(Dead ???)
3 Sockets in /run/screen/S-user.
"""


def test_parse_ls():
    s = screens.parse_ls(LS_OUTPUT)
    assert [x.id for x in s] == ["12345.pts-0.myhost", "460568.work", "123.old"]
    assert s[0].state == "Detached" and not s[0].attached
    assert s[1].attached
    assert s[2].dead and s[2].date == ""


def test_parse_info():
    info = screens.parse_info("(3,45)/(178,47)+1024 -flow mouse UTF-8 0(bash)")
    assert (info.cols, info.rows, info.number, info.title) == (178, 47, 0, "bash")
    assert screens.parse_info("garbage") is None


def test_parse_date_sort_order():
    assert screens.parse_date("11/02/26 19:16:07") > screens.parse_date("08/11/26 08:40:31") > 0
    assert screens.parse_date("") == 0


def test_attach_args():
    det = screens.Session(1, "a", "", "Detached")
    att = screens.Session(2, "b", "", "Attached")
    assert screens.attach_args("screen", det, "steal") == ["screen", "-r", "1.a"]
    assert screens.attach_args("screen", att, "steal") == ["screen", "-d", "-r", "2.b"]
    assert screens.attach_args("screen", att, "share") == ["screen", "-x", "2.b"]


def test_config_env():
    cfg = config.load({"HOME": "/h", "SCREENPICK_TILE_WIDTH": "40", "CODEX_HOME": "/c"})
    assert cfg.tile_width == 40
    assert cfg.claude_dir == Path("/h/.claude")
    assert cfg.codex_dir == Path("/c")
    with pytest.raises(ValueError):
        config.load({"HOME": "/h", "SCREENPICK_CAPTURE": "nope"})
    with pytest.raises(ValueError):
        config.load({"HOME": "/h", "SCREENPICK_REFRESH_SEC": "abc"})


def test_width_korean():
    assert width("한글a") == 5
    assert truncate("한글한글", 5) == "한글…"
    assert crop("한글한글", 5) == "한글"
    assert width(truncate("abcdef", 4)) <= 4


def test_parse_stat_with_spaces_in_comm():
    text = "123 (my (weird) prog) S 1 123 123 34816 4567 4194304 0 0 0 0 0 0 0 0 20 0 1 0 999 0 0"
    comm, ppid, tpgid = procinfo.parse_stat(text)
    assert (comm, ppid, tpgid) == ("my (weird) prog", 1, 4567)


def _proc(pid, ppid, comm, argv, tpgid=0):
    return procinfo.Proc(pid, ppid, comm, tpgid, argv)


def test_detect_agents():
    assert procinfo.is_claude(_proc(1, 0, "claude", ["claude", "--resume", "x"]))
    assert procinfo.is_codex(_proc(2, 0, "node", ["node", "/x/bin/codex", "resume"]))
    assert procinfo.is_codex(_proc(3, 0, "codex", ["/x/codex"]))
    assert not procinfo.is_codex(_proc(4, 0, "node", ["node", "server.js"]))


def test_descendants_and_foreground():
    procs = {
        10: _proc(10, 1, "bash", ["bash"], tpgid=20),
        20: _proc(20, 10, "node", ["node", "/bin/codex"]),
        21: _proc(21, 20, "codex", ["codex"]),
    }
    procs[10].children = [20]
    procs[20].children = [21]
    assert procinfo.foreground(procs, procs[10]).pid == 20
    assert [p.pid for p in procinfo.descendants(procs, 10)] == [10, 20, 21]


def test_claude_title_from_sessions_json(tmp_path):
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "42.json").write_text(json.dumps({"name": "내 세션", "cwd": "/w", "sessionId": "s"}))
    assert titles.claude_title(tmp_path, 42) == ("내 세션", "/w")


def test_claude_title_fallback_jsonl(tmp_path):
    sid = "f552422c-fb56-44ec-b1db-29010270223a"
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "7.json").write_text(json.dumps({"sessionId": sid, "cwd": "/w"}))
    proj = tmp_path / "projects" / "-w"
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text(
        json.dumps({"type": "user", "message": "hi"}) + "\n"
        + json.dumps({"type": "custom-title", "customTitle": "옛 제목"}) + "\n"
        + json.dumps({"type": "custom-title", "customTitle": "새 제목"}) + "\n"
    )
    assert titles.claude_title(tmp_path, 7) == ("새 제목", "/w")
    assert titles.claude_title(tmp_path, 999) == (None, None)


def _codex_db(path: Path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE threads (id TEXT, cwd TEXT, title TEXT, updated_at INTEGER)")
    con.executemany("INSERT INTO threads VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()


def test_codex_title_ignores_threads_older_than_process(tmp_path, monkeypatch):
    now = int(time.time())
    _codex_db(tmp_path / "state_5.sqlite", [
        ("old", "/w", "옛 대화", now - 10_000),
        ("new", "/w", "새 대화 제목\n둘째 줄", now),
    ])
    (tmp_path / "state_1.sqlite").write_text("")  # 낮은 버전은 무시해야 함
    monkeypatch.setattr(procinfo, "open_files", lambda pid: [])
    monkeypatch.setattr(procinfo, "start_time", lambda pid: now - 60)
    procs = {5: _proc(5, 1, "codex", ["codex"])}
    assert titles.codex_title(tmp_path, procs, 5, "/w") == "새 대화 제목"

    monkeypatch.setattr(procinfo, "start_time", lambda pid: now + 60)
    assert titles.codex_title(tmp_path, procs, 5, "/w") is None


def test_codex_title_by_open_rollout(tmp_path, monkeypatch):
    tid = "0190aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
    _codex_db(tmp_path / "state_5.sqlite", [(tid, "/w", "열린 스레드", 0)])
    monkeypatch.setattr(procinfo, "open_files",
                        lambda pid: [f"/x/rollout-2026-06-11T11-46-45-{tid}.jsonl"] if pid == 6 else [])
    procs = {5: _proc(5, 1, "node", ["node", "codex"]), 6: _proc(6, 5, "codex", ["codex"])}
    procs[5].children = [6]
    assert titles.codex_title(tmp_path, procs, 5, "/w") == "열린 스레드"


def test_layout_and_paging():
    L = tui.compute_layout(200, 50, 60, 14)
    assert L.cols == 3 and L.tile_w * 3 + 2 <= 200
    assert L.rows >= 2 and L.rows * L.tile_h + (L.rows - 1) <= 48
    tiny = tui.compute_layout(30, 10, 60, 14)
    assert tiny.per_page == 1


def test_thumbnail_keeps_bottom():
    lines = ["a", "b", "c", "", ""]
    assert tui.thumbnail(lines, 10, 2) == ["b", "c"]
    assert tui.thumbnail(["한글한글"], 5, 3) == ["한글"]


def test_render_survives_overwritten_wide_char():
    import pyte
    from screenpick.capture import render
    screen = pyte.Screen(10, 2)
    stream = pyte.Stream(screen)
    stream.feed("한글abc\r\n")
    stream.feed("\x1b[1;2Hx")  # '한' 의 오른쪽 절반을 덮어씀
    lines = render(screen)
    assert lines[0].endswith("글abc")


def test_session_name_rules():
    assert screens.check_session_name("work") is None
    assert screens.check_session_name("작업-1") is None
    assert screens.check_session_name("a b")
    assert screens.check_session_name("a.b")
    assert screens.check_session_name("a/b")
    assert screens.new_session_args("screen", "work") == ["screen", "-S", "work"]
    assert screens.new_session_args("screen", "") == ["screen"]


def test_attached_window_info_uses_most_recent_tty(monkeypatch):
    from screenpick import model
    procs = {1: _proc(1, 0, "screen", ["SCREEN"]), 10: _proc(10, 1, "bash", ["bash"]),
             11: _proc(11, 1, "bash", ["-bash"])}
    procs[1].children = [10, 11]
    monkeypatch.setattr(procinfo, "read_environ_var", lambda pid, name: {10: "0", 11: "3"}[pid])
    monkeypatch.setattr(procinfo, "tty_of", lambda pid: f"/dev/pts/{pid}")
    monkeypatch.setattr(procinfo, "tty_size", lambda path: (120, 40) if path.endswith("11") else (80, 24))
    monkeypatch.setattr(procinfo, "tty_activity", lambda path: (200.0, 0.0) if path.endswith("11") else (100.0, 0.0))
    info = model.attached_window_info(procs, 1)
    assert (info.cols, info.rows, info.number) == (120, 40, 3)
    assert model.attached_window_info({}, 1) is None


def test_is_screenpick():
    from screenpick import instance
    assert instance.is_screenpick(["screenpick"])
    assert instance.is_screenpick(["/x/venv/bin/python", "/home/u/.local/bin/screenpick"])
    assert instance.is_screenpick(["python3", "-m", "screenpick"])
    assert not instance.is_screenpick(["screenpick", "--list"])
    assert not instance.is_screenpick(["/bin/bash", "-c", "screenpick"])
    assert not instance.is_screenpick(["vim", "screenpick.py"])
    assert not instance.is_screenpick([])


def test_find_others(tmp_path):
    from screenpick import instance
    uid = __import__("os").getuid()
    for pid, argv in {10: ["screenpick"], 11: ["screenpick", "-l"], 12: ["bash"], 13: ["screenpick"]}.items():
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (tmp_path / "self").mkdir()
    assert instance.find_others(tmp_path, me=13, uid=uid) == [10]
    assert instance.find_others(tmp_path, me=13, uid=uid + 1) == []


def test_screen_children(tmp_path):
    from screenpick import instance
    for pid, comm, ppid in [(20, "screen", 10), (21, "bash", 10), (22, "screen", 99)]:
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1 0")
    assert instance.screen_children(10, tmp_path) == [20]

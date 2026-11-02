"""claude / codex 세션 제목 찾기."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from . import procinfo

log = logging.getLogger(__name__)

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ROLLOUT_RE = re.compile(rf"rollout-.*?({_UUID})\.jsonl$")

# (경로, mtime, 크기) -> 제목. 큰 jsonl 을 매번 다시 읽지 않기 위한 캐시.
_jsonl_cache: dict[tuple[str, float, int], str | None] = {}


def _first_line(text: str) -> str:
    return " ".join(text.strip().splitlines()[0].split()) if text and text.strip() else ""


# ---------------------------------------------------------------- claude

def _title_from_claude_jsonl(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime, st.st_size)
    if key in _jsonl_cache:
        return _jsonl_cache[key]
    custom = ai = summary = None
    try:
        with path.open("rb") as f:
            for raw in f:
                # 전체를 json 파싱하면 느리므로 후보 줄만 파싱한다.
                if b'"custom-title"' not in raw and b'"ai-title"' not in raw and b'"summary"' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                t = rec.get("type")
                if t == "custom-title" and rec.get("customTitle"):
                    custom = rec["customTitle"]
                elif t == "ai-title" and (rec.get("aiTitle") or rec.get("title")):
                    ai = rec.get("aiTitle") or rec.get("title")
                elif t == "summary" and rec.get("summary"):
                    summary = rec["summary"]
    except OSError:
        return None
    title = custom or ai or summary
    _jsonl_cache[key] = title
    return title


def claude_title(claude_dir: Path, pid: int) -> tuple[str | None, str | None]:
    """(제목, cwd). ~/.claude/sessions/<pid>.json 을 먼저 보고, 없으면 대화 기록에서 찾는다."""
    meta_path = claude_dir / "sessions" / f"{pid}.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None, None
    cwd = meta.get("cwd")
    if meta.get("name"):
        return _first_line(meta["name"]), cwd
    sid = meta.get("sessionId")
    if sid:
        for path in (claude_dir / "projects").glob(f"*/{sid}.jsonl"):
            title = _title_from_claude_jsonl(path)
            if title:
                return _first_line(title), cwd
    return None, cwd


# ---------------------------------------------------------------- codex

def _codex_db(codex_dir: Path) -> Path | None:
    def version(p: Path) -> int:
        m = re.search(r"state_(\d+)\.sqlite$", p.name)
        return int(m[1]) if m else -1
    dbs = sorted(codex_dir.glob("state_*.sqlite"), key=version)
    return dbs[-1] if dbs else None


def _query_codex(db: Path, sql: str, args: tuple) -> str | None:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
        try:
            row = con.execute(sql, args).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.info("codex DB 조회 실패: %s", exc)
        return None
    return row[0] if row else None


def codex_thread_id(procs: dict[int, procinfo.Proc], pid: int) -> str | None:
    """codex 프로세스(와 자식)가 열어 둔 rollout-*.jsonl 에서 스레드 id 를 얻는다."""
    for p in procinfo.descendants(procs, pid):
        for path in procinfo.open_files(p.pid):
            m = _ROLLOUT_RE.search(path)
            if m:
                return m[1]
    return None


def codex_title(codex_dir: Path, procs: dict[int, procinfo.Proc], pid: int, cwd: str | None) -> str | None:
    db = _codex_db(codex_dir)
    if not db:
        return None
    tid = codex_thread_id(procs, pid)
    title = None
    if tid:
        title = _query_codex(db, "SELECT title FROM threads WHERE id = ?", (tid,))
    if not title and cwd:
        # 열린 파일로 못 찾으면, 이 codex 가 시작된 뒤에 갱신된 같은 디렉터리 스레드 중 가장 최근 것.
        # (시작 전 스레드까지 보면 새로 연 빈 세션에 엉뚱한 옛 제목이 붙는다)
        started = procinfo.start_time(pid)
        if started is not None:
            title = _query_codex(
                db,
                "SELECT title FROM threads WHERE cwd = ? AND updated_at >= ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (cwd, int(started) - 5),
            )
    return _first_line(title) if title else None

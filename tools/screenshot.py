"""README 용 스크린샷(docs/screenshot.png)을 만든다.

실제 세션 대신 가짜 세션 9개를 만들어 screenpick 의 그리기 코드로 그대로 그린다.
개인 정보가 화면에 나오지 않게 하려는 것이다.

    uv run --with pillow python tools/screenshot.py [출력 경로]

동작: 가상 터미널(pty) 안에서 screenpick 화면을 띄우고, pyte 로 화면을 읽어
글꼴로 직접 그려 PNG 로 저장한다. 글꼴 경로는 SCREENSHOT_FONT_* 환경변수로 바꿀 수 있다.
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLS, ROWS = 186, 47

# ------------------------------------------------------------------ 가짜 세션

CLAUDE_BOTTOM = [
    "",
    "─" * 60,
    "> {prompt}",
    "─" * 60,
    "  >> accept edits on (shift+tab to cycle)",
]

FAKE = [
    dict(pid=41235, name="todo-app", state="Attached", kind="claude",
         title="todo-app-k2", cwd="~/projects/todo-app",
         lines=[
             "> 설정 화면에 다크 모드 토글을 추가해 줘",
             "",
             "* Update(src/settings/Toggle.tsx)",
             "  └ Added 18 lines",
             "",
             "* 다크 모드 토글을 추가했습니다.",
         ], prompt="테스트도 추가해 줘"),
    dict(pid=40877, name="weather-api", state="Detached", kind="codex",
         title="API 응답 캐시 적용", cwd="~/projects/weather-api",
         lines=[
             "> 같은 도시를 여러 번 조회하면 캐시를 쓰게 해 줘",
             "",
             "* Edited app/cache.py (+31 -2)",
             "* Edited app/routes.py (+6 -1)",
             "* Ran pytest -q",
             "  └ 24 passed in 0.82s",
             "",
             "캐시 유효 시간은 10분이며 CACHE_TTL 환경변수로",
             "바꿀 수 있습니다.",
             "",
             "> ",
         ]),
    dict(pid=40512, name="blog", state="Detached", kind="claude",
         title="블로그 글 맞춤법 검토", cwd="~/projects/blog",
         lines=[
             "* Read(posts/first-post.md)",
             "  └ Read 120 lines",
             "",
             "* 맞춤법 3곳과 띄어쓰기 5곳을 고쳤습니다.",
             "  - \"되요\" → \"돼요\"",
             "  - \"할수있다\" → \"할 수 있다\"",
         ], prompt="제목도 다듬어 줘"),
    dict(pid=39980, name="pts-1.devbox", state="Detached", kind="shell",
         title="~/projects/todo-app", cwd="~/projects/todo-app", detail="bash",
         lines=[
             "$ git log --oneline -5",
             "a1b2c3d 다크 모드 토글 추가",
             "9f8e7d6 설정 화면 레이아웃 정리",
             "5c4b3a2 할 일 정렬 옵션 추가",
             "1a2b3c4 README 업데이트",
             "0f9e8d7 첫 커밋",
             "$ git status",
             "On branch main",
             "nothing to commit, working tree clean",
             "$ ",
         ]),
    dict(pid=39421, name="logs", state="Detached", kind="program",
         title="~/projects/weather-api", cwd="~/projects/weather-api",
         detail="tail -f logs/app.log",
         lines=[
             "12:01:04 INFO  GET /weather?city=seoul 200 12ms",
             "12:01:09 INFO  GET /weather?city=busan 200 15ms",
             "12:01:11 INFO  GET /weather?city=seoul 200 1ms (cache)",
             "12:01:20 INFO  GET /forecast?city=jeju 200 31ms",
             "12:01:22 WARN  slow upstream response: 812ms",
             "12:01:35 INFO  GET /weather?city=daegu 200 14ms",
             "12:01:41 INFO  GET /weather?city=seoul 200 1ms (cache)",
             "12:01:58 INFO  GET /health 200 0ms",
         ]),
    dict(pid=38760, name="calculator", state="Detached", kind="claude",
         title="calculator-7f", cwd="~/projects/calculator",
         lines=[
             "> 나눗셈 테스트를 보강해 줘",
             "",
             "* Bash(pytest -q)",
             "  └ 18 passed in 0.11s",
             "",
             "* 0으로 나누기와 반올림 테스트를 추가했습니다.",
         ], prompt="커밋해 줘"),
    dict(pid=38102, name="pts-3.devbox", state="Detached", kind="shell",
         title="~", cwd="~", detail="bash",
         lines=[
             "$ ls",
             "Documents  Downloads  projects",
             "$ df -h /",
             "Filesystem      Size  Used Avail Use% Mounted on",
             "/dev/root        97G   41G   56G  43% /",
             "$ uptime",
             " 12:02:10 up 12 days,  2 users,  load average: 0.12",
             "$ ",
         ]),
    dict(pid=37655, name="csv-tools", state="Detached", kind="codex",
         title="CSV 파서 리팩터링", cwd="~/projects/csv-tools",
         lines=[
             "> 파서 함수를 작은 함수들로 나눠 줘",
             "",
             "* Edited csvtools/parser.py (+48 -61)",
             "* Ran python -m pytest",
             "  └ 12 passed in 0.40s",
             "",
             "parse_line, parse_header, coerce_types 세 함수로",
             "나눴고 동작은 그대로입니다.",
             "",
             "> ",
         ]),
    dict(pid=37019, name="docs-server", state="Detached", kind="program",
         title="~/projects/docs", cwd="~/projects/docs",
         detail="python -m http.server 8000",
         lines=[
             "Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0.0:8000/) ...",
             "127.0.0.1 - - \"GET / HTTP/1.1\" 200 -",
             "127.0.0.1 - - \"GET /style.css HTTP/1.1\" 200 -",
             "127.0.0.1 - - \"GET /guide/install.html HTTP/1.1\" 200 -",
             "127.0.0.1 - - \"GET /guide/usage.html HTTP/1.1\" 200 -",
         ]),
]


def fake_views():
    from screenpick import model, screens
    views = []
    for i, f in enumerate(FAKE):
        lines = list(f["lines"])
        if "prompt" in f:
            lines += [l.format(prompt=f["prompt"]) for l in CLAUDE_BOTTOM]
        detail = f.get("detail") or f"{f['kind']} · {f['cwd']}"
        views.append(model.SessionView(
            session=screens.Session(f["pid"], f["name"], f"11/06/26 12:{59 - i:02d}:00", f["state"]),
            kind=f["kind"], title=f["title"], detail=detail, cwd=f["cwd"],
            cols=60, rows=24, size_known=True, lines=lines, captured=True))
    return views


def child() -> None:
    """가상 터미널 안에서 가짜 데이터로 screenpick 화면을 띄운다."""
    sys.path.insert(0, str(ROOT / "src"))
    from screenpick import config, model, tui
    views = fake_views()
    model.collect = lambda cfg: [v for v in views]
    model.capture_views = lambda cfg, vs: None
    cfg = config.load({"HOME": os.environ.get("HOME", "/tmp"), "SCREENPICK_REFRESH_SEC": "0"})
    tui.run(cfg)


# ------------------------------------------------------------------ 화면 읽기

def capture_screen():
    import pyte
    env = {k: v for k, v in os.environ.items() if k not in ("STY", "WINDOW")}
    env.update(TERM="xterm-256color", LC_ALL="C.UTF-8")
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    proc = subprocess.Popen([sys.executable, __file__, "--child"], stdin=slave, stdout=slave,
                            stderr=slave, env=env, start_new_session=True)
    os.close(slave)
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.ByteStream(screen)
    end = time.time() + 3
    while time.time() < end:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                stream.feed(os.read(master, 65536))
            except OSError:
                break
    os.write(master, b"q")
    time.sleep(0.3)
    proc.kill()
    os.close(master)
    return screen


# ------------------------------------------------------------------ PNG 로 그리기

BG = (30, 34, 42)
FG = (220, 223, 228)
NAMED = {
    "black": (40, 44, 52), "red": (224, 108, 117), "green": (152, 195, 121), "brown": (229, 192, 123),
    "blue": (97, 175, 239), "magenta": (198, 120, 221), "cyan": (86, 182, 194), "white": (220, 223, 228),
}


def rgb(color: str, default):
    if color == "default":
        return default
    if color in NAMED:
        return NAMED[color]
    try:
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def find_ttc_index(path: str, family: str, size: int):
    from PIL import ImageFont
    for i in range(20):
        try:
            f = ImageFont.truetype(path, size, index=i)
        except OSError:
            break
        if f.getname()[0] == family:
            return f
    raise SystemExit(f"글꼴을 찾을 수 없습니다: {family} in {path}")


def render(screen, out: Path, size: int = 15) -> None:
    from PIL import Image, ImageDraw, ImageFont
    mono = os.environ.get("SCREENSHOT_FONT_MONO", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    mono_b = os.environ.get("SCREENSHOT_FONT_MONO_BOLD", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")
    cjk = os.environ.get("SCREENSHOT_FONT_CJK", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    cjk_b = os.environ.get("SCREENSHOT_FONT_CJK_BOLD", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
    cw = round(ImageFont.truetype(mono, size).getlength("M"))
    wide_size = round(cw * 2 * 0.94)  # 한글이 두 칸을 거의 채우도록
    fonts = {
        (False, False): ImageFont.truetype(mono, size), (True, False): ImageFont.truetype(mono_b, size),
        (False, True): find_ttc_index(cjk, "Noto Sans Mono CJK KR", wide_size),
        (True, True): find_ttc_index(cjk_b, "Noto Sans Mono CJK KR", wide_size),
    }
    ch = round(size * 1.5)
    pad = 16
    img = Image.new("RGB", (COLS * cw + pad * 2, ROWS * ch + pad * 2), BG)
    draw = ImageDraw.Draw(img)
    baseline = round(ch * 0.74)
    for y in range(ROWS):
        row = screen.buffer[y]
        for x in range(COLS):
            cell = row[x]
            if cell.data == "":
                continue  # 전각 문자의 오른쪽 절반
            wide = x + 1 < COLS and row[x + 1].data == ""
            fg, bg = rgb(cell.fg, FG), rgb(cell.bg, BG)
            if cell.reverse:
                fg, bg = bg, fg
            px, py = pad + x * cw, pad + y * ch
            cells = 2 if wide else 1
            if bg != BG:
                draw.rectangle([px, py, px + cw * cells - 1, py + ch - 1], fill=bg)
            c = cell.data
            if c == " ":
                continue
            if c in "─━│┃┌┐└┘┏┓┗┛":
                draw_box(draw, c, px, py, cw, ch, fg)
                continue
            font = fonts[(bool(cell.bold), wide)]
            gx = px + (cw * cells - font.getlength(c)) / 2 if wide else px
            draw.text((gx, py + baseline), c, font=font, fill=fg, anchor="ls")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)


def draw_box(draw, c, px, py, cw, ch, fg) -> None:
    """선 문자는 칸에 꼭 맞게 직접 그려 줄과 줄 사이가 끊기지 않게 한다."""
    heavy = c in "━┃┏┓┗┛"
    t = 2 if heavy else 1
    mx, my = px + cw // 2, py + ch // 2
    left, right = c in "─━┐┘┓┛", c in "─━┌└┏┗"
    up, down = c in "│┃└┘┗┛", c in "│┃┌┐┏┓"
    if left:
        draw.rectangle([px, my, mx, my + t - 1], fill=fg)
    if right:
        draw.rectangle([mx, my, px + cw, my + t - 1], fill=fg)
    if up:
        draw.rectangle([mx, py, mx + t - 1, my], fill=fg)
    if down:
        draw.rectangle([mx, my, mx + t - 1, py + ch], fill=fg)


def main() -> None:
    if "--child" in sys.argv:
        child()
        return
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshot.png"
    render(capture_screen(), out)
    print(f"저장했습니다: {out}")


if __name__ == "__main__":
    main()

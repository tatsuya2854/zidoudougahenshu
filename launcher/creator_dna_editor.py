#!/usr/bin/env python3
"""Creator DNA Editor ランチャー（ターミナル不要）。

start.sh / start.command / start.bat / Mac の .app は全部これを呼ぶ。セットアップのロジックはここに一本化する。

やること
  1. 依存確認（python3.11+ / ffmpeg / ffprobe。npm は画面ビルドに一度だけ）
  2. 初回セットアップ（.env 作成・backend/.venv 作成・pip install・frontend ビルド）
  3. サーバ起動 → /api/status が返るまで待つ → ブラウザを開く
     既に起動済み（ポートが応答する）ならブラウザを開くだけ
  進捗は data/launcher.log に書く（.app 起動時はターミナルが無いので、ここが唯一の手掛かり）。
  失敗したら macOS は日本語ダイアログ、他 OS は標準エラー。

オプション
  --check       依存確認だけして終了（0=OK, 1=不足あり）
  --foreground  サーバを前面で動かす（ターミナルから起動するとき。Ctrl+C で停止）
  --no-browser  ブラウザを開かない
  --stop        このランチャーが起動したサーバ（data/server.pid）を止める

標準ライブラリだけで動く（venv が無い状態で最初に走るスクリプトなので）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"
LOG_PATH = DATA / "launcher.log"
SERVER_LOG = DATA / "server.log"
PID_PATH = DATA / "server.pid"
VENV = BACKEND / ".venv"
REQ_STAMP = VENV / ".requirements.sha256"
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
APP_NAME = "Creator DNA Editor"
MIN_PY = (3, 11)


# ────────────────────────── ログ / 通知 ──────────────────────────


class Log:
    def __init__(self, echo: bool) -> None:
        self.echo = echo
        DATA.mkdir(parents=True, exist_ok=True)
        self.f = LOG_PATH.open("a", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        self.f.write(line + "\n")
        self.f.flush()
        if self.echo:
            print(msg, flush=True)


def notify_error(title: str, msg: str) -> None:
    """ターミナルが無い起動（.app）でも見えるように、macOS はダイアログ。"""
    sys.stderr.write(f"{title}: {msg}\n")
    if IS_MAC and shutil.which("osascript"):
        safe = msg.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display dialog "{safe}" with title "{title}" buttons {{"OK"}} default button 1 with icon stop'],
                       check=False, capture_output=True)


# ────────────────────────── 環境 ──────────────────────────


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def server_addr() -> tuple[str, int]:
    """HOST / PORT だけ .env から読む（他のキーは読まない・表示しない）。環境変数が優先。"""
    host, port = "127.0.0.1", 8765
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = (x.strip() for x in line.split("=", 1))
            v = v.strip("'\"")
            if k == "HOST" and v:
                host = v
            elif k == "PORT" and v.isdigit():
                port = int(v)
    host = os.environ.get("HOST", host)
    port = int(os.environ.get("PORT", port) or port)
    return host, port


def is_ready(url: str) -> bool:
    """/api/status がこのアプリの形（providers を持つ）で返れば起動済み。"""
    try:
        with urlopen(url + "/api/status", timeout=1) as r:
            return "providers" in json.load(r)
    except Exception:  # noqa: BLE001
        return False


def check_deps(log: Log) -> list[str]:
    """不足している必須ツールの一覧を返す（空なら OK）。"""
    missing: list[str] = []
    py = sys.version_info[:2]
    log(f"python: {platform.python_version()} ({sys.executable})")
    if py < MIN_PY:
        missing.append(f"Python {MIN_PY[0]}.{MIN_PY[1]} 以上（今は {py[0]}.{py[1]}）")
    for tool in ("ffmpeg", "ffprobe"):
        p = shutil.which(tool)
        log(f"{tool}: {p or '見つかりません'}")
        if not p:
            missing.append(tool)
    npm = shutil.which("npm")
    log(f"npm: {npm or '無し（画面ビルド済みなら不要）'}")
    if not npm and not (FRONTEND / "dist" / "index.html").exists():
        log("警告: npm が無く frontend/dist も無いので、画面をビルドできません（API だけ動きます）")
    return missing


# ────────────────────────── セットアップ ──────────────────────────


def run(cmd: list[str], log: Log, cwd: Path | None = None) -> None:
    log(f"$ {' '.join(cmd)}")
    with LOG_PATH.open("a", encoding="utf-8") as out:
        subprocess.run(cmd, cwd=cwd or ROOT, stdout=out, stderr=subprocess.STDOUT, check=True)


def _deps_importable(py: str) -> bool:
    """venv に主要パッケージが入っているか（requirements のスタンプが無い既存環境の判定用）。"""
    return subprocess.run([py, "-c", "import fastapi, uvicorn, sqlmodel, cv2, yaml"], capture_output=True).returncode == 0


def setup(log: Log) -> None:
    if not (ROOT / ".env").exists() and (ROOT / ".env.example").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
        log(".env を作成しました。APIキーを入れると本番品質になります（未設定でもモックで動きます）")

    req = BACKEND / "requirements.txt"
    req_hash = hashlib.sha256(req.read_bytes()).hexdigest() if req.exists() else ""
    if not venv_python().exists():
        log("Python 環境を作成中…（初回のみ、数分かかります）")
        run([sys.executable, "-m", "venv", str(VENV)], log)
    py = str(venv_python())
    stamp = REQ_STAMP.read_text().strip() if REQ_STAMP.exists() else ""
    if req_hash and not stamp and _deps_importable(py):
        # 旧 start.sh や uv で作った既存 venv（スタンプ無し・pip 無しの可能性）はそのまま使う
        REQ_STAMP.write_text(req_hash)
        stamp = req_hash
    if req_hash and stamp != req_hash:
        log("依存パッケージをインストール中…")
        if subprocess.run([py, "-m", "pip", "--version"], capture_output=True).returncode != 0:
            run([py, "-m", "ensurepip", "--upgrade"], log)
        run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"], log)
        run([py, "-m", "pip", "install", "-q", "-r", str(req)], log)
        REQ_STAMP.write_text(req_hash)

    if not (FRONTEND / "dist" / "index.html").exists():
        npm = shutil.which("npm")
        if npm:
            log("画面をビルド中…（初回のみ）")
            run([npm, "install", "--silent"], log, cwd=FRONTEND)
            run([npm, "run", "build", "--silent"], log, cwd=FRONTEND)
        else:
            log("npm が無いので画面をビルドできません。Node.js を入れるか、ビルド済み frontend/dist を配置してください")


# ────────────────────────── サーバ ──────────────────────────


def start_server(log: Log, foreground: bool) -> subprocess.Popen:
    env = dict(os.environ)
    env["OPEN_BROWSER"] = "false"  # ブラウザはランチャーが開く（起動確認後）
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    cmd = [str(venv_python()), "-m", "app"]
    log(f"$ {' '.join(cmd)}  (cwd={BACKEND})")
    if foreground:
        proc = subprocess.Popen(cmd, cwd=BACKEND, env=env)
    else:
        out = SERVER_LOG.open("a", encoding="utf-8")
        kw: dict = {"start_new_session": True} if not IS_WIN else {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        proc = subprocess.Popen(cmd, cwd=BACKEND, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
    PID_PATH.write_text(str(proc.pid))
    return proc


def wait_ready(url: str, proc: subprocess.Popen, log: Log, timeout: float = 90.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if is_ready(url):
            return True
        if proc.poll() is not None:
            log(f"サーバが終了しました (code={proc.returncode})")
            return False
        time.sleep(0.3)
    return False


def stop_server(log: Log) -> int:
    if not PID_PATH.exists():
        log("server.pid がありません（このランチャーから起動したサーバは無い）")
        return 1
    pid = int(PID_PATH.read_text().strip() or 0)
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        log(f"サーバを停止しました (pid={pid})")
    except ProcessLookupError:
        log(f"pid={pid} は既に終了しています")
    PID_PATH.unlink(missing_ok=True)
    return 0


# ────────────────────────── main ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=f"{APP_NAME} launcher")
    ap.add_argument("--check", action="store_true", help="依存確認だけして終了")
    ap.add_argument("--foreground", action="store_true", help="サーバを前面で動かす（Ctrl+C で停止）")
    ap.add_argument("--no-browser", action="store_true", help="ブラウザを開かない")
    ap.add_argument("--stop", action="store_true", help="起動済みサーバを止める")
    args = ap.parse_args()

    log = Log(echo=args.foreground or args.check or args.stop or sys.stdout.isatty())
    log(f"--- {APP_NAME} launcher ({platform.system()} {platform.release()}) root={ROOT}")

    if args.stop:
        return stop_server(log)

    missing = check_deps(log)
    if args.check:
        if missing:
            log("不足: " + ", ".join(missing))
            return 1
        log("依存 OK")
        return 0
    if missing:
        notify_error(APP_NAME, "次が見つかりません: " + ", ".join(missing) + "\nREADME の「必要なもの」を確認してください。")
        return 1

    host, port = server_addr()
    url = f"http://{host}:{port}"

    if is_ready(url):
        log(f"起動済み: {url}")
        if not args.no_browser:
            webbrowser.open(url + "/")
        return 0

    try:
        setup(log)
    except subprocess.CalledProcessError as e:
        notify_error(APP_NAME, f"セットアップに失敗しました（{' '.join(e.cmd[:3])}…）。\n{LOG_PATH} を確認してください。")
        return 1

    log(f"起動中… {url}/")
    proc = start_server(log, foreground=args.foreground)
    if not wait_ready(url, proc, log):
        if proc.poll() is None:
            proc.terminate()
        where = LOG_PATH if args.foreground else SERVER_LOG
        notify_error(APP_NAME, f"サーバが起動しませんでした。\n{where} を確認してください。")
        return 1

    log(f"起動しました: {url}/")
    if not args.no_browser:
        webbrowser.open(url + "/")

    if args.foreground:
        log("停止するには Ctrl+C")
        try:
            return proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return 0
        finally:
            PID_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

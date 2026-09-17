#!/usr/bin/env bash
# Creator DNA Editor 起動スクリプト（Mac / Linux）
# ダブルクリック or `./start.sh`。初回は依存を入れる。以降はサーバを起動してブラウザを開く。
set -euo pipefail
cd "$(dirname "$0")"

need() { command -v "$1" >/dev/null 2>&1 || { echo "❌ $1 が見つかりません。README の「必要なもの」を確認してください。"; exit 1; }; }
need python3
need ffmpeg
need ffprobe

if [ ! -f .env ]; then
  cp .env.example .env
  echo "ℹ️  .env を作成しました。APIキーを入れると本番品質になります（未設定でもモックで動きます）。"
fi

# --- backend ---
if [ ! -d backend/.venv ]; then
  echo "📦 Python 環境を作成中…"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q --upgrade pip
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi

# --- frontend（ビルド済みでなければビルド）---
if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "🎨 画面をビルド中…"
    (cd frontend && npm install --silent && npm run build --silent)
  else
    echo "⚠️  npm が無いので画面をビルドできません。Node.js を入れるか、ビルド済み frontend/dist を配置してください。"
  fi
fi

echo "🚀 起動中… ブラウザが自動で開きます（開かなければ http://127.0.0.1:8765/ ）"
cd backend && exec .venv/bin/python -m app

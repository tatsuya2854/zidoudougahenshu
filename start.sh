#!/usr/bin/env bash
# Creator DNA Editor 起動スクリプト（Mac / Linux）。実体は launcher/creator_dna_editor.py
# `./start.sh` で前面起動（Ctrl+C で停止）。初回は依存を入れて画面をビルドし、ブラウザが自動で開く。
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "python3 が見つかりません。README の「必要なもの」を確認してください。"; exit 1; }
exec python3 launcher/creator_dna_editor.py --foreground "$@"

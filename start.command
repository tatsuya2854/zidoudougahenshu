#!/usr/bin/env bash
# Creator DNA Editor 起動スクリプト（Mac、Finder でダブルクリック）。実体は launcher/creator_dna_editor.py
# ターミナルを出したくない場合は launcher/build_mac_app.sh で .app を作る。
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "python3 が見つかりません。README の「必要なもの」を確認してください。"; read -r -p "Enter で閉じる"; exit 1; }
python3 launcher/creator_dna_editor.py --foreground "$@" || read -r -p "エラーで終了しました。Enter で閉じる"

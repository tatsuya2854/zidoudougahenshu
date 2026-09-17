# Creator DNA Editor

YouTuber ごとの「話し方」と「編集者の判断」を学習し、その人専属の AI 編集者として Shorts を作るシステム。
**Phase 1（長尺 → 文字起こし → 候補10本 → 選択 → 9:16 → 字幕 → MP4、GUI 完結）を実装済み。**

設計ドキュメントは [`docs/`](docs/) にまとめてある（調査・構成・データ構造・Creator DNA / Editing DNA スキーマ・実装計画・コスト・リスク）。

## 必要なもの
- Python 3.11+
- ffmpeg / ffprobe（libass, libx264 入りの通常ビルド）
  - Mac: `brew install ffmpeg` / Windows: gyan.dev の full build を PATH に / Ubuntu: `apt install ffmpeg fonts-noto-cjk`
- Node.js 20+（画面のビルドに一度だけ使う）
- 日本語フォント（Mac/Windows は標準で可。Linux は `fonts-noto-cjk`）

## 起動（日常運用はこれだけ）
```
./start.sh        # Mac / Linux
start.bat         # Windows
```
初回だけ依存をインストールして画面をビルドし、ブラウザで `http://127.0.0.1:8765/` が開く。

## APIキー
`.env.example` を `.env` にコピーして入れる（サーバ側だけが読む。フロントには出ない。git 管理外）。
```
ANTHROPIC_API_KEY=...   # 候補選定（claude-opus-5）
OPENAI_API_KEY=...      # 文字起こし（whisper-1 / gpt-4o-transcribe-diarize）
```
未設定でも **モック動作** で全工程が動く（候補は発話密度ベースの仮候補）。

ローカル文字起こしにしたい場合: `pip install -r backend/requirements-local.txt` + `.env` で `TRANSCRIPTION_PROVIDER=faster_whisper`。

## 使い方
1. 左の「＋ Creator を追加」→ 名前と **Creator Dictionary**（方言・口癖・人名・商品名）を入れる
2. 長尺動画をドラッグ＆ドロップ
3. 「解析して Shorts 候補を出す」→ 候補 10 本がスコア・根拠付きで並ぶ
4. ▶ プレビューで確認、「採用する」にチェック（👍👎 は学習データ）
5. 「AI編集して一括書き出し」→ 完成した Shorts をその場で再生・ダウンロード
6. 右下に動画ごとの推定原価

## 構成
```
backend/   FastAPI + SQLite。app/providers が Provider Adapter Layer（外部 SDK はここだけ）
frontend/  React + Vite（ビルド物は backend が同一ポートで配信）
docs/      設計ドキュメント
data/      動画・DB・書き出し（git 管理外）
```

## 開発
```
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest            # mock provider で E2E
OPEN_BROWSER=false .venv/bin/python -m app
cd frontend && npm install && npm run dev   # 開発サーバ（/api は 8765 にプロキシ）
```

## ロードマップ
Phase 2 Creator DNA → Phase 3 A/B 比較で Editing DNA → Phase 4 DNA 駆動の自動編集 → Phase 5 人間修正からの継続学習 → Phase 6 複数 Creator / SaaS。

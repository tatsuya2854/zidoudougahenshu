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
./start.sh        # Linux / Mac（ターミナルから。Ctrl+C で停止）
start.command     # Mac（Finder でダブルクリック。ターミナルが開く）
start.bat         # Windows（ダブルクリック）
```
初回だけ依存をインストールして画面をビルドし、ブラウザで `http://127.0.0.1:8765/` が開く。
どれも中身は `launcher/creator_dna_editor.py` を呼ぶだけ（セットアップのロジックはそこに一本化）。進捗・エラーは `data/launcher.log`。

**Mac でターミナルを出したくない場合（ネイティブ .app）**
```
./launcher/build_mac_app.sh          # リポジトリ直下に「Creator DNA Editor.app」ができる
```
Finder でダブルクリック → 起動確認後にブラウザが開く。既に起動済みならブラウザを開くだけ。失敗時は日本語ダイアログを出し、詳細は `data/launcher.log` / `data/server.log`。
Dock や「アプリケーション」に置いても動く（ビルド時のリポジトリの場所を覚えている。リポジトリを移動したら作り直す）。
署名・公証はしていない。自分の Mac で生成した .app はそのまま開ける。別の Mac に配った場合は右クリック → 開く。
停止は `python3 launcher/creator_dna_editor.py --stop`（依存確認だけなら `--check`）。

## APIキー
`.env.example` を `.env` にコピーして入れる（サーバ側だけが読む。フロントには出ない。git 管理外）。
```
ANTHROPIC_API_KEY=...   # 候補選定（claude-opus-5）
OPENAI_API_KEY=...      # 文字起こし（whisper-1 / gpt-4o-transcribe-diarize）
```
**文字起こしは APIキー無しでも本物が動く。** `TRANSCRIPTION_PROVIDER=auto`（既定）は
OPENAI_API_KEY があれば OpenAI、無ければローカルの faster-whisper（`FASTER_WHISPER_MODEL`、既定 `large-v3-turbo`。
初回だけモデルを自動ダウンロード）、それも無ければモック。画面右上の「文字起こし」バッジと `/api/status` の `reason` で
どれが選ばれたか分かる。GPU で速くしたい場合は `backend/requirements-local.txt` のメモを参照。

LLM キーが無い場合、候補選定は **モック動作**（発話密度ベースの仮候補）になる。

自前の字幕（YouTube の字幕など）がある場合は SRT を読み込める:
`POST /api/videos/{id}/transcript/srt`（multipart `file`、UTF-8）→ `POST /api/videos/{id}/analyze?reuse_transcript=true`
で文字起こしを飛ばして候補選定だけ行う（文字起こし原価は記録されない）。

## 使い方
1. 左の「＋ Creator を追加」→ 名前と **Creator Dictionary**（方言・口癖・人名・商品名）を入れる
2. 長尺動画をドラッグ＆ドロップ
3. 「解析して Shorts 候補を出す」→ 候補 10 本がスコア・根拠付きで並ぶ
4. ▶ プレビューで確認、「採用する」にチェック（👍👎 は学習データ）
5. 候補カードの「✏️ 編集」で開始/終了・タイトル・構図（話者追従/中央/ぼかし/手動）・字幕の位置/サイズ/本文を手直しできる（「編集済み」バッジ。手直しは学習データとして記録）
6. 自前の字幕があれば「📄 SRT を読み込む」→「🔁 再解析」で文字起こし無しに候補を出し直せる
7. 「AI編集して一括書き出し」→ 完成した Shorts をその場で再生・ダウンロード。「🗜 まとめて ZIP」で MP4＋SRT＋編集計画＋原価を一括保存
8. 右下に動画ごとの推定原価

**まとめてダウンロード（ZIP）**: 完成品は MP4 だけでなく、同じ分割の SRT 字幕と編集計画 JSON を付けて 1 つの ZIP で落とせる。
```
GET /api/videos/{video_id}/bundle.zip      # その動画の完成品すべて
GET /api/exports/{export_id}/bundle.zip    # 1 本分
GET /api/exports/{export_id}/srt           # SRT だけ（?download=true でファイル名付き）
```
ZIP の中身: `01_タイトル/xxx.mp4`（字幕焼き込み済み）, `xxx.srt`（クリップ相対時刻。編集ソフトで手直しする用）, `xxx.edit_plan.json`（採用区間・リフレーム・字幕スタイル）, ルートに `README.txt`（動画・Creator・生成日時・推定原価）と `costs.json`（原価内訳）。

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

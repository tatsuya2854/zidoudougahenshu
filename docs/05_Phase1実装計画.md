# 05. Phase 1 実装計画（→ 実装済み）

## ゴール
長尺動画 → 文字起こし → Shorts 候補10本 → 人間が選択 → 9:16 → 字幕 → MP4。GUI だけで完結。

## 実装ステップと状態

| # | ステップ | 状態 | 場所 |
|---|---|---|---|
| 1 | 設定 / Secrets / Provider Registry（キー未設定なら mock フォールバック） | ✅ | `app/config.py`, `app/providers/registry.py` |
| 2 | DB スキーマ（Creator 分離、原価台帳、修正ログ） | ✅ | `app/models.py` |
| 3 | Transcript / Candidate / CreatorDNA / EditingDNA スキーマ | ✅ | `app/schemas/` |
| 4 | 文字起こしアダプタ: OpenAI(whisper-1 / diarize) / faster-whisper / mock | ✅ | `app/providers/transcription/` |
| 5 | LLM アダプタ: Anthropic（構造化出力 + キャッシュ）/ OpenAI / mock | ✅ | `app/providers/llm/` |
| 6 | 候補選定プロンプト（Creator らしさ・単体成立・過去 Shorts 類似を評価軸に） | ✅ | `app/prompts/candidate_selection.md`, `services/candidates.py` |
| 7 | 顔検出（YuNet）→ 話者追従クロップ（EMA + デッドゾーン + 話者交代ジャンプ） | ✅ | `services/reframe.py` |
| 8 | ASS 字幕生成（句読点/文字数/間で分割、Noto Sans CJK JP、pop/fade） | ✅ | `providers/caption/ass_caption.py` |
| 9 | ffmpeg レンダ（sendcmd で時間可変 crop → scale → subtitles → x264） | ✅ | `providers/video/ffmpeg_proc.py` |
| 10 | ジョブランナー + 進捗 + 再起動時の復旧 | ✅ | `services/jobs.py` |
| 11 | API（creators / videos / candidates / exports / jobs / costs / status） | ✅ | `app/api/` |
| 12 | GUI（D&D → 解析 → 候補カード → プレビュー → 採用 → 書き出し → DL → 原価） | ✅ | `frontend/src/App.tsx` |
| 13 | E2E テスト（mock provider で全工程） | ✅ | `backend/tests/test_pipeline_mock.py` |
| 14 | 起動スクリプト（Mac/Linux/Windows） | ✅ | `start.sh`, `start.bat` |

## 動作確認済み
- `pytest`: アップロード → 解析 → 候補10本 → 2本書き出し（1080×1920、ASS 焼き込み）→ blur_fit 書き出し → 原価台帳。
- 実ブラウザ（Chromium）で GUI を通し、5本一括書き出しまで完走。
- sendcmd による時間可変クロップ + 日本語字幕の描画を出力フレームで目視確認。

## 未確認（実キーが必要）
- Anthropic / OpenAI 実 API での候補選定品質と方言保持率 → **最初の実動画で必ず確認**（07 参照）。
- YuNet の実写顔での追従品質（合成映像では顔が無いため center フォールバックのみ検証）。

## Phase 1 の既知の割り切り
- 候補区間は LLM の start/end をそのまま使う（無音詰め・ジャンプカットは Phase4）。
- 字幕は文単位（語単位強調は Phase4、Editing DNA 依存）。
- 複数人の「誰が喋っているか」は未対応（最大顔を追う）。
- 進捗はポーリング。SSE/WebSocket は Phase6。

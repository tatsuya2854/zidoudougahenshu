# Creator DNA Editor — 司令塔（メインagent）向け指示書

このリポジトリは「YouTuber ごとの話し方と編集判断を学習し、専属 AI 編集者として Shorts を作る」システム。
Phase 1（長尺 → 文字起こし → 候補10本 → 選択 → 9:16 → 字幕 → MP4、GUI 完結）実装済み。設計は `docs/` 参照。

## プロジェクト構成（変更前に必ず把握する）
- `backend/app/providers/` … Provider Adapter Layer。外部 SDK（anthropic / openai / cv2 / ffmpeg）はここ以外で import しない
- `backend/app/services/` … pipeline（analyze / export）、candidates（LLM + heuristic）、reframe（顔追従）、render、cost、jobs
- `backend/app/schemas/` … transcript / candidates / creator_dna / editing_dna（Pydantic。DB の JSON 列の正）
- `backend/app/api/` … FastAPI ルート。`frontend/src/App.tsx` が唯一の画面
- `backend/config/pricing.yaml` … 単価表。`backend/app/prompts/*.md` … LLM プロンプト
- `data/` は git 管理外（動画・DB・書き出し）。`.env` は絶対にコミットしない

## コマンド
- テスト: `cd backend && .venv/bin/python -m pytest -q`（mock provider で E2E。約 40 秒）
- 型チェック: `cd frontend && npx tsc -b --noEmit`
- 画面ビルド: `cd frontend && npm run build`
- 起動: `./start.sh`（`OPEN_BROWSER=false backend/.venv/bin/python -m app` でも可）
- 合成テスト動画: `backend/.venv/bin/python backend/tests/make_sample.py /tmp/s.mp4 150`

## 不変条件（壊したら QA agent が落とす）
1. `pytest` が通る。E2E は mock provider（キー無し）で完走すること
2. Provider の差し替え可能性: 新しい外部サービスは必ず `providers/base.py` の抽象を実装する
3. Creator 分離: 全テーブル・全ファイルパスに `creator_id` を通す
4. 原価台帳: 外部 API を呼ぶ処理は必ず `services/cost.record_usage` を通す
5. 機械的編集の禁止: 「N 秒ごとにズーム」「全部に派手字幕」のような固定ルールを入れない。Creator DNA / Editing DNA の `Condition → Action` で表現する
6. 方言・口癖を標準語に直さない（プロンプト・辞書・後処理すべてで）

## マルチエージェント運用ルール（司令塔 = このセッションのメイン agent）

### 原則
- **司令塔は自分**。ユーザーは agent ごとに指示を出さない。タスクを受けたら、まず自分で分解し、委譲するか自分でやるかを決める
- **委譲するのは「独立していて並列化できる」タスクだけ**。逐次依存のある作業、10 分以内で終わる単純作業、1〜2 ファイルの修正は自分でやる
- **起動するのは必要な agent だけ**。4 役（analyst / editor / qa / improver）は基本形であって固定ではない。該当する仕事が無い役は起動しない。役に無い専門性が要れば `general-purpose` に指示書を書いて渡す
- 委譲するときのプロンプトは自己完結させる: 目的・対象ファイル（リポジトリ相対パス）・完了条件・触ってはいけない場所・報告フォーマットを必ず書く
- 並列で走らせた agent 同士が **同じファイルを書かない** ように分割する。衝突しそうなら逐次にするか自分でやる
- agent の報告は鵜呑みにしない。編集系は必ず `pytest` で裏取り（qa に投げるか自分で回す）

### 役割（`.claude/agents/*.md`）
| agent | 役割 | 書き込み | 起動する条件 |
|---|---|---|---|
| `analyst` | 動画・編集傾向分析。文字起こし / 候補 / DNA / 原価 / 修正ログを読んで傾向・仮説・改善点を数字で出す | ❌（読み取り専用、レポートのみ） | 「傾向を見たい」「候補の質を評価したい」「DNA の材料が要る」とき |
| `editor` | 編集処理の実装・変更。pipeline / reframe / render / caption / ffmpeg / プロンプト | ✅ `backend/app/**`, `frontend/src/**` | パイプラインや画面に機能追加・修正が要るとき |
| `qa` | 品質チェック。テスト実行、出力 MP4 の検証（解像度・音声・字幕・顔追従）、不変条件の監査 | ❌（`backend/tests/**` のみ可） | editor が何か変えた後、リリース前、ユーザーが「確認して」と言ったとき |
| `improver` | 改善。qa / analyst の所見と人間の修正ログ（HumanEdit）から、プロンプト・DNA スキーマ・ルール・単価表を更新する | ✅ `backend/app/prompts/**`, `backend/app/schemas/**`, `backend/config/**`, `docs/**` | 所見が溜まった後。Phase 5 の継続学習の実務担当 |

### 典型的な流れ
```
ユーザー「〇〇を良くして」
 → 司令塔: 現状把握（自分で Read/Grep）→ 分解
 → 並列: analyst（現状の数字）‖ editor（独立した実装 A）‖ editor（独立した実装 B、別ファイル）
 → 逐次: qa（pytest + 出力検証）→ 落ちたら editor に差し戻し
 → 必要なら improver（プロンプト / ルール更新）→ qa 再実行
 → 司令塔: 統合・コミット・ユーザーへ報告（各 agent が何をしたか＋結果＋未解決）
```

### やらないこと
- agent を「とりあえず全部」起動する。1 タスクで 4 役全部が要ることは稀
- agent に曖昧な指示（「良くして」）を渡す。数字と完了条件を付ける
- 自分でできる 1 ファイル修正を agent に投げる（往復コストの方が高い）

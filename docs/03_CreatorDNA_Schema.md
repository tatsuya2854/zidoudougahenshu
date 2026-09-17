# 03. Creator DNA スキーマ

実体: `backend/app/schemas/creator_dna.py`（Pydantic）。`creator.creator_dna` に JSON で保存。

## 設計原則

1. **証拠付き**: 全ての特徴に `Evidence { video_id, start_sec, end_sec, quote }`。LLM の思い込みを後から検証・棄却できる。
2. **分布で持つ**: 話速・音量・間は平均だけでなく p10/p90。テンションは baseline と「上がる条件」。
3. **文字起こしに直結**: `dictionary` は ASR の prompt/hotwords に流れる。DNA が育つほど文字起こしが正確になる（正のループ）。
4. **プロンプト用要約を固定文で持つ**: `summary_for_prompt` を system prompt に入れてキャッシュ。同じ Creator の動画を連続処理すると入力コストが 1/10。
5. **confidence**: 解析した分数（`analyzed_minutes`）に応じた信頼度。少数サンプルで断定させない。

## 構造

```
CreatorDNA
├─ version, creator_id, analyzed_video_ids[], analyzed_minutes, confidence
├─ dictionary[]: DictionaryEntry           … 方言/口癖/語尾/人名/ブランド/商品名/地名/専門用語
│    { term, reading, category, standard_form(直すとこうなる=直さない記録), note, frequency_per_10min, evidence[] }
├─ speech: SpeechStyle                     … 方言名, 一人称, 語尾, 口癖(entry), 言い回し, 視聴者への呼びかけ, フィラー
├─ prosody: ProsodyProfile                 … 話速(文字/秒 mean/p10/p90), LUFS mean/range, 間の中央値/p90,
│                                             意図的な間の文脈[], 笑い方, 笑い頻度
├─ emotion: EmotionProfile                 … baseline_tension, テンションが上がる話題[], 感情変化の型[], リアクションの型[]
├─ humor: HumorProfile                     … ツッコミの型[], ボケの型[], 持ちネタ[], 自虐度
├─ structure: StructureProfile             … 冒頭フックの型[], 話題転換の型[], 盛り上げ方[], オチの型[], 締めの型[]
├─ signature_moments[]: SignatureMoment    … 「その人らしい発言」 { description, quote, why_signature, evidence }
├─ past_shorts: PastShortsPattern          … 実際に Shorts 化された部分の傾向 { typical_duration, content_types[], hook_types[], examples[] }
└─ summary_for_prompt                      … 300〜600字の固定要約
```

要求仕様の解析対象との対応:

| 要求 | フィールド |
|---|---|
| 方言・口癖・語尾・言い回し | `speech.*`, `dictionary[category in dialect/catchphrase/sentence_ending]` |
| 固有名詞・商品名・人物名 | `dictionary[category in person/brand/product/place]` |
| 話す速度・テンション・声量 | `prosody.speaking_rate_*`, `emotion.baseline_tension`, `prosody.loudness_*` |
| 感情変化・笑い方 | `emotion.emotion_transition_patterns`, `prosody.laugh_style` |
| 沈黙・「間」の取り方 | `prosody.pause_*`, `prosody.intentional_pause_contexts` |
| ツッコミ・ボケ・リアクション | `humor.*`, `emotion.reaction_patterns` |
| 話題転換・盛り上がる瞬間・冒頭フック・オチ | `structure.*` |
| 視聴者に語りかけるパターン | `speech.address_to_viewer` |
| その人らしい発言 | `signature_moments[]` |
| 過去動画で Shorts 化された部分 | `past_shorts` |

## 生成方法（Phase 2 の計画）

1. 過去動画 N 本（推奨 10 本以上 / 5 時間以上）を Phase1 パイプラインで文字起こし。
2. 音声特徴量をローカル算出（ffmpeg `ebur128`/`silencedetect`、話速 = 文字数/発話秒）。笑い検出は Scribe v2 の audio events か、YAMNet 系の分類器。
3. LLM に「動画1本ずつ」構造化抽出（`CreatorDNA` の部分オブジェクト）→ **マージ用 LLM 呼び出し** で統合。証拠の無い項目は捨てる。
4. Creator の実 Shorts（本人提供 or YouTube Data API の自チャンネル）があれば `past_shorts` を埋める。
5. `summary_for_prompt` を生成 → Phase1 の候補選定 system prompt に自動挿入（実装済み: `services/candidates.build_system_prompt`）。

## Phase 1 で今使っている部分

- `creator.dictionary`（GUI の Creator 設定で入力）→ ASR の語彙ヒント + LLM の system prompt。
- `creator_dna.summary_for_prompt` があれば候補選定に混ぜる（今は空）。

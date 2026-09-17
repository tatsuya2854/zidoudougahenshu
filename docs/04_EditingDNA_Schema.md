# 04. Editing DNA スキーマ

実体: `backend/app/schemas/editing_dna.py`。`creator.editing_dna` に JSON 保存。

## 設計原則

- **固定ルール禁止**: 「N 秒ごとにカット」は持たない。全て `Condition（文脈）→ Action（操作）` の `EditingRule`。
- **観測と規則を分ける**: A/B 比較で得た生の差分は `SourceComparison`（観測）。そこから LLM が抽出した規則が `EditingRule`（仮説）。仮説は `confidence / observed_count / counter_count / evidence` を持つ。
- **適用結果も構造化**: 自動編集は `EditPlan`（EDL: `EditDecision[]`）を出す。人間の修正は EditDecision 単位で `accepted / modified / removed` を付け、Phase5 の学習入力になる。

## 3 層構造

```
[観測]  SourceComparison  … 元動画A × 公開動画B の差分（1ペアに1つ）
   │  alignment[]        … B の各区間が A のどこ由来か（順序入替も表現）
   │  cuts[]             … A にあって B に無い区間 + inferred_reason(silence/filler/repetition/mistake/off_topic/low_energy/pacing)
   │  kept_silences[]    … 残された沈黙 + 前後の文脈 + 推定理由（オチ前の溜め等）
   │  captions[]         … テロップ { time, text, emphasized_words[], style_id, position, inferred_purpose }
   │  effects[]          … zoom_in/out, pan, shake, flash, freeze, speed, image_insert { trigger_text, purpose }
   │  audio_cues[]       … se / bgm_start / bgm_change / bgm_stop / ducking { label, trigger_text, purpose }
   │  opening_choice / ending_choice / retained_ratio
   ▼
[規則]  EditingDNA
   │  rules[]: EditingRule { id, description, condition: Condition, action: Action, confidence, observed_count, counter_count, evidence[], feedback_log[], active }
   │     Condition … content_type[], emotion[], tension_min/max, speaker, keyword_patterns[], silence_min/max_sec, position_in_clip, after_event
   │     Action    … kind ∈ {cut, keep, tighten_silence, keep_silence, caption, emphasize_word, zoom_in, zoom_out, pan, se, bgm_change, bgm_stop, image_insert, freeze, speed_up, move_to_opening, end_here}, params{}, caption_style_id
   │  pacing: PacingProfile { silence_target_sec_default, silence_target_by_context{}, jump_cut_tolerance, shot_length_by_tension{}, filler_removal, reaction_keep_policy, time_to_punchline_sec_median }
   │  caption_styles[]: CaptionStyle { font, weight, size_ratio, colors, outline, emphasis_color/scale, position, margin, max_chars/lines, min/max duration, animation }
   │  shorts_policy: ShortsPolicy { opening_strategy[], ending_strategy[], target_duration, reframe_style, caption_density }
   │  se_library[], bgm_library[], summary_for_prompt, confidence
   ▼
[適用]  EditPlan { candidate_id, keep_ranges[], decisions[]: EditDecision { action, time_start, time_end, rule_id, reason, human_status }, caption_style_id, reframe_style }
```

### ルールの例（実際にこういう JSON になる）

```json
{
  "id": "r-keep-silence-after-boke",
  "description": "ボケの直後の 0.6〜1.5 秒の沈黙は残す（相方のリアクション待ち）",
  "condition": {"after_event": "boke", "silence_min_sec": 0.6, "silence_max_sec": 1.5},
  "action": {"kind": "keep_silence", "params": {}},
  "confidence": 78, "observed_count": 14, "counter_count": 2,
  "evidence": [{"video_id": "abc", "start_sec": 812.4, "end_sec": 813.5, "quote": "…って、なんでやねん（沈黙）"}]
}
```
```json
{
  "id": "r-emph-price",
  "description": "金額を言った語だけ黄色・1.3倍で強調（商品紹介パート限定）",
  "condition": {"content_type": ["商品紹介"], "keyword_patterns": ["\\d+円", "なんぼ"]},
  "action": {"kind": "emphasize_word", "params": {"scale": 1.3}, "caption_style_id": "default"},
  "confidence": 65, "observed_count": 9, "counter_count": 0
}
```

要求仕様との対応:

| 要求（編集者の判断） | 置き場所 |
|---|---|
| なぜ削除したか / 沈黙を残したか / 無音をどこまで詰めるか | `cuts[].inferred_reason`, `kept_silences[]`, `pacing.silence_target_by_context` |
| どこでジャンプカット | `pacing.jump_cut_tolerance`, rule(action=cut, condition) |
| 強調した発言 / テロップ位置 / 色を変えた単語 / フォント / サイズ / 表示位置 / 表示時間 | `captions[]`, `caption_styles[]`, rule(action=caption / emphasize_word) |
| ズーム / SE / BGM のタイミング | `effects[]`, `audio_cues[]`, rule(action=zoom_in / se / bgm_change) |
| リアクションを残す基準 | `pacing.reaction_keep_policy` + rule(action=keep) |
| オチまでのテンポ | `pacing.time_to_punchline_sec_median` |
| Shorts の冒頭に何を持ってくるか | `opening_choice`, `shorts_policy.opening_strategy`, rule(action=move_to_opening) |

## 生成方法（Phase 3 の計画）

1. A と B を両方文字起こし → **テキスト + 音声指紋で整列**（B の各セグメントを A に写像。順序入替・繰り返しも許容）。
2. 差分から `cuts / kept_silences / retained_ratio` を機械的に算出（LLM 不要）。
3. B のシーン境界（PySceneDetect）+ 前後フレームを Claude Vision に渡し、テロップ文言・強調語・位置・ズーム有無を抽出 → `captions / effects`。
4. B の音声から SE/BGM 変化を検出（スペクトル変化 + 音源分離は Phase4 以降。初期は「BGM 有無の変化」のみ）。
5. `SourceComparison` を LLM に渡し、**文脈と操作の関係** を `EditingRule[]` として言語化。observed_count の少ない規則は `active=false`。
6. 複数ペアで規則をマージ、`confidence` を更新。

## Phase 5 の更新ロジック（設計）

- 人間の修正 = `EditPlan.decisions[].human_status` の変化 + 新規 decision。
- diff ごとに LLM で「なぜ直したか」を推定（`HumanEdit.inferred_reason`）。
- 同じ `rule_id` に対する `removed/modified` が k 回（既定 3）続いたら `counter_count++` → confidence を下げ、閾値未満で `active=false`。逆に `accepted` は `observed_count++`。
- 新規 decision が同じ文脈で繰り返されたら **新ルール候補** として生成し、confidence 低で追加。

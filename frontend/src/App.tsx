import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { ApiError, api, effective, fmtDur, fmtTime, isEdited, uploadVideo } from './api'
import type { Candidate, CaptionCue, CostSummary, Creator, CropMode, DictEntry, Export, Job, Status, Video } from './api'

// ──────────────────────────── ジョブのポーリング ────────────────────────────
function useJob(jobId: string | null, onDone?: (j: Job) => void) {
  const [job, setJob] = useState<Job | null>(null)
  const cb = useRef(onDone)
  cb.current = onDone
  useEffect(() => {
    if (!jobId) {
      setJob(null)
      return
    }
    let alive = true
    const tick = async () => {
      try {
        const j = await api.job(jobId)
        if (!alive) return
        setJob(j)
        if (j.status === 'done' || j.status === 'failed') {
          cb.current?.(j)
          return
        }
      } catch {
        /* retry */
      }
      if (alive) setTimeout(tick, 1000)
    }
    tick()
    return () => {
      alive = false
    }
  }, [jobId])
  return job
}

// ──────────────────────────── Creator 編集モーダル ────────────────────────────
function dictToText(d: DictEntry[]) {
  return d.map((e) => [e.term, e.category, e.note ?? ''].join(' | ').replace(/ \| $/, '')).join('\n')
}
function textToDict(t: string): DictEntry[] {
  return t
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const [term, category, note] = l.split('|').map((s) => s.trim())
      return { term, category: category || 'other', note: note || '' }
    })
}

function CreatorModal({ creator, onClose, onSaved }: { creator: Creator | null; onClose: () => void; onSaved: (c: Creator) => void }) {
  const [name, setName] = useState(creator?.name ?? '')
  const [notes, setNotes] = useState(creator?.notes ?? '')
  const [dict, setDict] = useState(creator ? dictToText(creator.dictionary) : '')
  const cs = (creator?.caption_style ?? {}) as Record<string, string | number>
  const [position, setPosition] = useState(String(cs.position ?? 'bottom'))
  const [animation, setAnimation] = useState(String(cs.animation ?? 'none'))
  const [fontSize, setFontSize] = useState(String(cs.font_size_ratio ?? 0.035))
  const [emph, setEmph] = useState(String(cs.emphasis_color ?? '#FFD400'))
  const [err, setErr] = useState('')
  const save = async () => {
    if (!name.trim()) return setErr('名前は必須')
    const body = {
      name,
      notes,
      dictionary: textToDict(dict),
      caption_style: { position, animation, font_size_ratio: Number(fontSize) || 0.035, emphasis_color: emph },
    }
    try {
      const c = creator ? await api.updateCreator(creator.id, body) : await api.createCreator(body)
      onSaved(c)
    } catch (e) {
      setErr(String(e))
    }
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{creator ? 'Creator 設定' : 'Creator を追加'}</h2>
        <div>
          <label>名前（チャンネル名）</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例: 〇〇チャンネル" />
        </div>
        <div>
          <label>メモ（ジャンル・スタイル・NG など。LLM に渡る）</label>
          <textarea value={notes ?? ''} onChange={(e) => setNotes(e.target.value)} placeholder="例: 関西弁のガジェット系。ツッコミ多め。企業案件は別テンション。" />
        </div>
        <div>
          <label>Creator Dictionary（1行1語: 語 | 種類 | メモ）— 方言・口癖・人名・商品名を標準語に直させないための辞書</label>
          <textarea
            value={dict}
            onChange={(e) => setDict(e.target.value)}
            placeholder={'なんぼ | dialect | 「いくら」の意\n〜やねん | sentence_ending\nタナカくん | person | 相方\niPhone 17 Pro | product'}
            style={{ minHeight: 140, fontFamily: 'monospace' }}
          />
          <div className="muted" style={{ fontSize: 11 }}>種類: dialect / catchphrase / sentence_ending / person / brand / product / place / jargon / other</div>
        </div>
        <div className="row">
          <div className="grow">
            <label>字幕の位置</label>
            <select value={position} onChange={(e) => setPosition(e.target.value)}>
              <option value="bottom">下</option>
              <option value="center">中央</option>
              <option value="top">上</option>
            </select>
          </div>
          <div className="grow">
            <label>字幕アニメ</label>
            <select value={animation} onChange={(e) => setAnimation(e.target.value)}>
              <option value="none">なし</option>
              <option value="pop">ポップ</option>
              <option value="fade">フェード</option>
            </select>
          </div>
          <div className="grow">
            <label>文字サイズ（高さ比）</label>
            <input value={fontSize} onChange={(e) => setFontSize(e.target.value)} />
          </div>
          <div className="grow">
            <label>強調色</label>
            <input value={emph} onChange={(e) => setEmph(e.target.value)} />
          </div>
        </div>
        {err && <div className="error">{err}</div>}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="ghost" onClick={onClose}>キャンセル</button>
          <button className="primary" onClick={save}>保存</button>
        </div>
      </div>
    </div>
  )
}

// ──────────────────────────── ドロップゾーン ────────────────────────────
function DropZone({ creatorId, onUploaded }: { creatorId: string; onUploaded: (v: Video) => void }) {
  const [over, setOver] = useState(false)
  const [prog, setProg] = useState<number | null>(null)
  const [err, setErr] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const handle = async (files: FileList | null) => {
    if (!files || !files.length) return
    setErr('')
    for (const f of Array.from(files)) {
      setProg(0)
      try {
        const v = await uploadVideo(creatorId, f, setProg)
        onUploaded(v)
      } catch (e) {
        setErr(String(e))
      }
    }
    setProg(null)
  }
  return (
    <div
      className={'dropzone' + (over ? ' over' : '')}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        handle(e.dataTransfer.files)
      }}
      onClick={() => inputRef.current?.click()}
    >
      <input ref={inputRef} type="file" accept="video/*,.mkv,.mov,.mp4" multiple style={{ display: 'none' }} onChange={(e) => handle(e.target.files)} />
      {prog === null ? (
        <>
          <strong>ここに長尺動画をドラッグ＆ドロップ</strong>
          mp4 / mov / mkv。クリックして選択もできます。
        </>
      ) : (
        <>
          <strong>アップロード中 {Math.round(prog * 100)}%</strong>
          <div className="progress" style={{ marginTop: 8 }}><div style={{ width: `${prog * 100}%` }} /></div>
        </>
      )}
      {err && <div className="error" style={{ marginTop: 8 }}>{err}</div>}
    </div>
  )
}

// ──────────────────────────── 候補カード ────────────────────────────
function CandidateCard({ c, selected, editing, onToggle, onPreview, onDecide, onEdit }: {
  c: Candidate
  selected: boolean
  editing: boolean
  onToggle: () => void
  onPreview: () => void
  onDecide: (d: Candidate['decision']) => void
  onEdit: () => void
}) {
  const d = c.data
  const eff = effective(c)
  const dur = eff.end - eff.start
  const edited = isEdited(c)
  return (
    <div className={'cand' + (selected ? ' selected' : '') + (c.decision === 'rejected' ? ' rejected' : '') + (editing ? ' editing' : '')}>
      <div className="thumb">
        <img src={`/api/candidates/${c.id}/thumb`} alt="" onError={(e) => ((e.target as HTMLImageElement).style.visibility = 'hidden')} />
        <button className="small" onClick={onPreview}>▶ プレビュー</button>
        <button className={'small' + (editing ? ' primary' : '')} onClick={onEdit}>✏️ 編集</button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text)', cursor: 'pointer' }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={selected} onChange={onToggle} /> 採用する
        </label>
        <div className="row" style={{ gap: 4 }}>
          <button className={'small' + (c.decision === 'accepted' ? ' primary' : ' ghost')} onClick={() => onDecide('accepted')}>👍</button>
          <button className={'small' + (c.decision === 'rejected' ? ' primary' : ' ghost')} onClick={() => onDecide('rejected')}>👎</button>
        </div>
      </div>
      <div>
        <div className="head">
          <span className="badge">#{c.rank}</span>
          <span className={'score' + (c.score >= 80 ? ' hi' : '')}>{Math.round(c.score)}</span>
          <span className="title">{eff.title}</span>
          <span className="badge">{fmtTime(eff.start)} → {fmtTime(eff.end)}（{dur.toFixed(0)}秒）</span>
          {edited && <span className="badge accent" title={'手直し: ' + Object.keys(c.overrides).join(', ')}>編集済み</span>}
          {d.standalone_ok ? <span className="badge ok">単体OK</span> : <span className="badge warn">要文脈</span>}
          {d.tags?.map((t) => <span key={t} className="tag">#{t}</span>)}
        </div>
        <div className="quote" style={{ marginTop: 8 }}>「{d.hook}」</div>
        {d.hook_suggestion && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>冒頭差し替え案: {d.hook_suggestion}</div>}
        <dl className="kv">
          <dt>内容</dt><dd>{d.summary}</dd>
          <dt>切り抜く価値</dt><dd>{d.why_clip}</dd>
          <dt>Creatorらしさ</dt><dd>{d.creator_likeness_reason}</dd>
          <dt>単体で成立</dt><dd>{d.standalone_reason || (d.standalone_ok ? 'はい' : 'いいえ')}</dd>
          <dt>視聴維持</dt><dd>{d.retention_reason}</dd>
          {d.title_alternatives?.length > 0 && (<><dt>タイトル別案</dt><dd>{d.title_alternatives.join(' ／ ')}</dd></>)}
        </dl>
        <div className="subscores">
          {Object.entries(d.scores ?? {}).map(([k, v]) => (
            <span key={k}>{SCORE_LABEL[k] ?? k} <b>{v}</b></span>
          ))}
        </div>
      </div>
    </div>
  )
}
const SCORE_LABEL: Record<string, string> = {
  hook_strength: 'フック', standalone: '単体成立', creator_likeness: 'らしさ', retention: '維持', payoff: 'オチ', past_shorts_similarity: '過去Shorts類似',
}

// ──────────────────────────── 候補の手直し ────────────────────────────
// 開始/終了・タイトル・構図・字幕の位置/サイズ・字幕本文を GUI で直す。保存は PUT /overrides（部分更新）。
// サーバ側で HumanEdit に before/after が残るので、ここでの手直しはそのまま Phase5 の学習データになる。
const CROP_LABEL: Record<CropMode, string> = { face_track: '話者追従', center: '中央', blur_fit: '全体表示＋ぼかし', manual: '手動（左右を指定）' }

function EditPanel({ c, video, videoRef, onSaved, onClose, onPreview, notify }: {
  c: Candidate
  video: Video
  videoRef: RefObject<HTMLVideoElement | null>
  onSaved: (c: Candidate) => void
  onClose: () => void
  onPreview: (start: number, end: number) => void
  notify: (msg: string) => void
}) {
  const o = c.overrides ?? {}
  const [start, setStart] = useState(String(o.start_sec ?? c.start_sec))
  const [end, setEnd] = useState(String(o.end_sec ?? c.end_sec))
  const [title, setTitle] = useState(o.title ?? '')
  const [cropMode, setCropMode] = useState<CropMode | ''>(o.crop_mode ?? '')
  const [cropX, setCropX] = useState(o.crop_x ?? 0.5)
  const [fontSize, setFontSize] = useState(o.font_size_ratio != null ? String(o.font_size_ratio) : '')
  const [position, setPosition] = useState(o.caption_position ?? '')
  const [cues, setCues] = useState<CaptionCue[] | null>(null)
  const [cuesEdited, setCuesEdited] = useState(!!o.captions)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  // 候補が切り替わったらフォームを詰め直し、字幕の初期値（自動分割 or 手直し済み）を取る
  useEffect(() => {
    const ov = c.overrides ?? {}
    setStart(String(ov.start_sec ?? c.start_sec))
    setEnd(String(ov.end_sec ?? c.end_sec))
    setTitle(ov.title ?? '')
    setCropMode(ov.crop_mode ?? '')
    setCropX(ov.crop_x ?? 0.5)
    setFontSize(ov.font_size_ratio != null ? String(ov.font_size_ratio) : '')
    setPosition(ov.caption_position ?? '')
    setCuesEdited(!!ov.captions)
    setErr('')
    setCues(null)
    api.candidateCaptions(c.id).then((r) => setCues(r.cues)).catch((e) => setErr(String(e)))
    panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [c.id, c.overrides, c.start_sec, c.end_sec])

  const s = Number(start)
  const e = Number(end)
  const dur = e - s
  const now = () => videoRef.current?.currentTime ?? 0
  const seek = (t: number) => {
    const el = videoRef.current
    if (el) {
      el.currentTime = t
      el.pause()
    }
  }
  const save = async () => {
    setErr('')
    if (!Number.isFinite(s) || !Number.isFinite(e)) return setErr('開始・終了は秒数で入力してください')
    setSaving(true)
    try {
      const body = {
        start_sec: s !== c.start_sec ? s : null,
        end_sec: e !== c.end_sec ? e : null,
        title: title.trim() ? title.trim() : null,
        crop_mode: cropMode || null,
        crop_x: cropMode === 'manual' ? cropX : null,
        font_size_ratio: fontSize.trim() ? Number(fontSize) : null,
        caption_position: position || null,
        captions: cuesEdited && cues ? cues : null,
      }
      const saved = await api.putOverrides(c.id, body)
      onSaved(saved)
      notify('手直しを保存しました（学習データとして記録）')
      // 保存後は親から新しい candidate が渡り、上の effect が区間に合わせて字幕を取り直す
    } catch (ex) {
      setErr(String(ex instanceof Error ? ex.message : ex))
    } finally {
      setSaving(false)
    }
  }
  const reset = async () => {
    if (!confirm('この候補の手直しをすべて捨てて AI 案に戻しますか？')) return
    try {
      const saved = await api.resetOverrides(c.id)
      onSaved(saved)
      notify('AI 案に戻しました')
    } catch (ex) {
      setErr(String(ex instanceof Error ? ex.message : ex))
    }
  }
  const updateCue = (i: number, patch: Partial<CaptionCue>) => {
    if (!cues) return
    setCuesEdited(true)
    setCues(cues.map((q, k) => (k === i ? { ...q, ...patch } : q)))
  }
  const removeCue = (i: number) => {
    if (!cues) return
    setCuesEdited(true)
    setCues(cues.filter((_, k) => k !== i))
  }
  const addCue = () => {
    const t = Math.min(Math.max(now(), s), e - 0.5)
    setCuesEdited(true)
    setCues([...(cues ?? []), { start: Math.round(t * 10) / 10, end: Math.round(Math.min(t + 2, e) * 10) / 10, text: '' }].sort((a, b) => a.start - b.start))
  }
  const durBad = !(dur >= 5 && dur <= 90)

  return (
    <div className="card editpanel" ref={panelRef}>
      <div className="row">
        <h3 style={{ fontSize: 13 }}>✏️ #{c.rank} を手直し</h3>
        {isEdited(c) && <span className="badge accent">編集済み</span>}
        <span className="grow" />
        <button className="small ghost" onClick={onClose}>閉じる</button>
      </div>
      <div className="muted" style={{ fontSize: 11 }}>直した内容は before/after で記録され、あなたの編集判断として学習に使われます。</div>

      <div className="row">
        <div className="grow">
          <label>開始（秒）</label>
          <input type="number" step="0.1" min={0} max={video.duration_sec} value={start} onChange={(ev) => setStart(ev.target.value)} />
          <div className="row" style={{ gap: 4, marginTop: 4 }}>
            <button className="small ghost" onClick={() => setStart(now().toFixed(1))}>⏺ 今の位置を開始に</button>
            <button className="small ghost" onClick={() => seek(s)}>⏵ 開始へ</button>
          </div>
        </div>
        <div className="grow">
          <label>終了（秒）</label>
          <input type="number" step="0.1" min={0} max={video.duration_sec} value={end} onChange={(ev) => setEnd(ev.target.value)} />
          <div className="row" style={{ gap: 4, marginTop: 4 }}>
            <button className="small ghost" onClick={() => setEnd(now().toFixed(1))}>⏺ 今の位置を終了に</button>
            <button className="small ghost" onClick={() => seek(e)}>⏵ 終了へ</button>
          </div>
        </div>
      </div>
      <div className="row" style={{ fontSize: 12 }}>
        <span className={durBad ? 'error' : 'muted'}>{fmtTime(s || 0)} → {fmtTime(e || 0)}（{Number.isFinite(dur) ? dur.toFixed(1) : '?'}秒 / 5〜90秒）</span>
        <span className="grow" />
        <button className="small" onClick={() => onPreview(s, e)}>▶ この範囲を再生</button>
      </div>

      <div>
        <label>タイトル（空なら AI 案「{c.data?.title || c.title}」）</label>
        <input value={title} onChange={(ev) => setTitle(ev.target.value)} placeholder={c.data?.title || c.title} />
      </div>

      <div className="row">
        <div className="grow">
          <label>構図（9:16）</label>
          <select value={cropMode} onChange={(ev) => setCropMode(ev.target.value as CropMode | '')}>
            <option value="">書き出し設定に従う</option>
            {(Object.keys(CROP_LABEL) as CropMode[]).map((k) => <option key={k} value={k}>{CROP_LABEL[k]}</option>)}
          </select>
        </div>
        <div className="grow">
          <label>字幕の位置</label>
          <select value={position} onChange={(ev) => setPosition(ev.target.value as typeof position)}>
            <option value="">Creator 設定に従う</option>
            <option value="top">上</option>
            <option value="center">中央</option>
            <option value="bottom">下</option>
          </select>
        </div>
        <div className="grow">
          <label>字幕サイズ（高さ比 0.015〜0.12）</label>
          <input type="number" step="0.005" min={0.015} max={0.12} value={fontSize} onChange={(ev) => setFontSize(ev.target.value)} placeholder="Creator 設定" />
        </div>
      </div>
      {cropMode === 'manual' && (
        <div>
          <label>左右の位置: {cropX < 0.45 ? `左寄り ${Math.round(cropX * 100)}%` : cropX > 0.55 ? `右寄り ${Math.round(cropX * 100)}%` : '中央'}</label>
          <input type="range" min={0} max={1} step={0.01} value={cropX} onChange={(ev) => setCropX(Number(ev.target.value))} />
          <div className="row muted" style={{ justifyContent: 'space-between', fontSize: 11 }}><span>左端</span><span>中央</span><span>右端</span></div>
          <div className="cropguide" style={{ aspectRatio: `${video.width || 16}/${video.height || 9}` }}>
            <div style={{ left: `${cropX * (1 - (9 / 16) * ((video.height || 9) / (video.width || 16))) * 100}%`, width: `${(9 / 16) * ((video.height || 9) / (video.width || 16)) * 100}%` }} />
          </div>
        </div>
      )}

      <div>
        <div className="row" style={{ marginBottom: 4 }}>
          <label style={{ margin: 0 }}>字幕本文（{cuesEdited ? '手直し中' : '自動分割'}）</label>
          <span className="grow" />
          <button className="small ghost" onClick={addCue} disabled={!cues}>＋ 追加</button>
          {cuesEdited && (
            <button className="small ghost" onClick={async () => { setCuesEdited(false); await api.putOverrides(c.id, { captions: null }).then(onSaved).catch(() => {}); const r = await api.candidateCaptions(c.id); setCues(r.cues) }}>自動分割に戻す</button>
          )}
        </div>
        {!cues ? (
          <div className="muted" style={{ fontSize: 12 }}>読み込み中…</div>
        ) : cues.length === 0 ? (
          <div className="muted" style={{ fontSize: 12 }}>この区間に字幕はありません。</div>
        ) : (
          <div className="cues">
            {cues.map((q, i) => (
              <div key={i} className="cue">
                <div className="row" style={{ gap: 4 }}>
                  <input type="number" step="0.1" value={q.start} onChange={(ev) => updateCue(i, { start: Number(ev.target.value) })} />
                  <span className="muted">→</span>
                  <input type="number" step="0.1" value={q.end} onChange={(ev) => updateCue(i, { end: Number(ev.target.value) })} />
                  <button className="small ghost" title="ここへシーク" onClick={() => seek(q.start)}>⏵</button>
                  <button className="small ghost" title="この字幕を消す" onClick={() => removeCue(i)}>×</button>
                </div>
                <textarea rows={2} value={q.text} onChange={(ev) => updateCue(i, { text: ev.target.value })} placeholder="（空なら表示しない）" />
              </div>
            ))}
          </div>
        )}
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>方言・口癖はそのままに。改行はそのまま 2 行目になります。</div>
      </div>

      {err && <div className="error">{err}</div>}
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button className="ghost small" onClick={reset} disabled={!isEdited(c)}>AI 案に戻す</button>
        <button className="primary" onClick={save} disabled={saving || durBad}>{saving ? '保存中…' : '保存'}</button>
      </div>
    </div>
  )
}

// ──────────────────────────── コスト ────────────────────────────
const CAT_LABEL: Record<string, string> = { transcription: '文字起こし', llm: 'LLM', vision: '映像解析', video_processing: '動画処理', storage: 'ストレージ' }
function CostPanel({ cost, title }: { cost: CostSummary | null; title: string }) {
  if (!cost) return null
  return (
    <div className="card">
      <h3 style={{ fontSize: 13, marginBottom: 8 }}>{title}</h3>
      <div className="cost">
        {Object.entries(cost.by_category).map(([k, v]) => (<><span key={k} className="muted">{CAT_LABEL[k] ?? k}</span><span key={k + 'v'}>${v.toFixed(4)}</span></>))}
        <span className="total">合計推定原価</span><span className="total">${cost.total_usd.toFixed(4)}</span>
      </div>
    </div>
  )
}

// ──────────────────────────── 本体 ────────────────────────────
export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [creators, setCreators] = useState<Creator[]>([])
  const [creatorId, setCreatorId] = useState<string | null>(null)
  const [modal, setModal] = useState<'new' | 'edit' | null>(null)
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState<string | null>(null)
  const [cands, setCands] = useState<Candidate[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [exportsList, setExports] = useState<Export[]>([])
  const [analyzeJobId, setAnalyzeJobId] = useState<string | null>(null)
  const [exportJobId, setExportJobId] = useState<string | null>(null)
  const [cost, setCost] = useState<CostSummary | null>(null)
  const [reframe, setReframe] = useState('face_track')
  const [captions, setCaptions] = useState(true)
  const [preview, setPreview] = useState<{ start: number; end: number } | null>(null)
  const [err, setErr] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [toast, setToast] = useState('')
  const videoRef = useRef<HTMLVideoElement>(null)
  const srtInputRef = useRef<HTMLInputElement>(null)
  const notify = useCallback((msg: string) => setToast(msg), [])
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 4000)
    return () => clearTimeout(t)
  }, [toast])

  const creator = creators.find((c) => c.id === creatorId) ?? null
  const video = videos.find((v) => v.id === videoId) ?? null

  const loadCreators = useCallback(async () => {
    const cs = await api.creators()
    setCreators(cs)
    if (!creatorId && cs.length) setCreatorId(cs[0].id)
  }, [creatorId])

  const loadVideos = useCallback(async (cid: string) => setVideos(await api.videos(cid)), [])
  const loadVideoDetail = useCallback(async (vid: string) => {
    const [c, e, co, v] = await Promise.all([api.candidates(vid), api.exports(vid), api.videoCost(vid), api.video(vid)])
    setCands(c)
    setExports(e)
    setCost(co)
    if (v.latest_job && (v.latest_job.status === 'running' || v.latest_job.status === 'queued')) setAnalyzeJobId(v.latest_job.id)
  }, [])

  useEffect(() => {
    api.status().then(setStatus).catch(() => setStatus(null))
    loadCreators()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    if (creatorId) {
      loadVideos(creatorId)
      setVideoId(null)
      setCands([])
      setExports([])
      setSelected(new Set())
    }
  }, [creatorId, loadVideos])
  useEffect(() => {
    if (videoId) {
      setSelected(new Set())
      setPreview(null)
      setEditingId(null)
      setAnalyzeJobId(null)
      setExportJobId(null)
      loadVideoDetail(videoId)
    }
  }, [videoId, loadVideoDetail])

  const analyzeJob = useJob(analyzeJobId, (j) => {
    if (videoId) {
      loadVideoDetail(videoId)
      loadVideos(creatorId!)
    }
    if (j.status === 'failed') setErr(j.error ?? '解析に失敗')
  })
  const exportJob = useJob(exportJobId, (j) => {
    if (videoId) {
      loadVideoDetail(videoId)
      loadVideos(creatorId!)
    }
    if (j.status === 'failed') setErr(j.error ?? '書き出しに失敗')
  })
  const busy = (analyzeJob && (analyzeJob.status === 'running' || analyzeJob.status === 'queued')) || (exportJob && (exportJob.status === 'running' || exportJob.status === 'queued'))

  // 書き出し中はエクスポート一覧を随時更新
  useEffect(() => {
    if (!exportJobId || !videoId) return
    const t = setInterval(() => api.exports(videoId).then(setExports).catch(() => {}), 2000)
    return () => clearInterval(t)
  }, [exportJobId, videoId])

  // プレビュー: 開始位置へシークして終了で止める
  useEffect(() => {
    const el = videoRef.current
    if (!el || !preview) return
    el.currentTime = preview.start
    el.play().catch(() => {})
    const onTime = () => {
      if (el.currentTime >= preview.end) {
        el.pause()
        el.currentTime = preview.start
      }
    }
    el.addEventListener('timeupdate', onTime)
    return () => el.removeEventListener('timeupdate', onTime)
  }, [preview])

  const startAnalyze = async () => {
    if (!videoId) return
    setErr('')
    try {
      const j = await api.analyze(videoId)
      setAnalyzeJobId(j.id)
    } catch (e) {
      setErr(String(e))
    }
  }
  const startExport = async () => {
    if (!videoId || !selected.size) return
    setErr('')
    try {
      const r = await api.createExports(videoId, Array.from(selected), { reframe_style: reframe, captions })
      setExportJobId(r.job.id)
      setExports(await api.exports(videoId))
    } catch (e) {
      setErr(String(e))
    }
  }
  const toggle = (id: string) => {
    const s = new Set(selected)
    if (s.has(id)) s.delete(id)
    else s.add(id)
    setSelected(s)
  }
  const decide = async (c: Candidate, d: Candidate['decision']) => {
    const nd = c.decision === d ? 'pending' : d
    await api.decide(c.id, nd)
    setCands(cands.map((x) => (x.id === c.id ? { ...x, decision: nd } : x)))
    if (nd === 'accepted') setSelected(new Set(selected).add(c.id))
    if (nd === 'rejected') {
      const s = new Set(selected)
      s.delete(c.id)
      setSelected(s)
    }
  }

  const onCandidateSaved = (saved: Candidate) => setCands(cands.map((x) => (x.id === saved.id ? saved : x)))
  // 別 editor が実装中の契約。無いサーバでは 404 → トーストで案内（画面は壊さない）
  const importSrt = async (file: File) => {
    if (!videoId) return
    try {
      await api.importSrt(videoId, file)
      notify('SRT を読み込みました。候補を出し直すには再解析してください')
      loadVideoDetail(videoId)
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405)) notify('SRT 読み込みはこのサーバではまだ使えません')
      else notify('SRT 読み込みに失敗: ' + String(e instanceof Error ? e.message : e))
    }
  }
  const downloadBundle = async () => {
    if (!videoId) return
    const ok = await api.bundleAvailable(videoId).catch(() => false)
    if (!ok) return notify('まとめて ZIP ダウンロードはこのサーバではまだ使えません')
    window.location.href = api.bundleUrl(videoId)
  }
  const editing = cands.find((c) => c.id === editingId) ?? null

  const stepOf = () => {
    if (!video) return 0
    if (video.status !== 'analyzed' && !analyzeJob) return 1
    if (analyzeJob && analyzeJob.status !== 'done') return 1
    if (exportsList.some((e) => e.status === 'done')) return 4
    if (selected.size) return 3
    return 2
  }
  const step = stepOf()
  const mock = status && (status.providers.llm.effective === 'mock' || status.providers.transcription.effective === 'mock')

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">Creator DNA Editor<small>専属AI編集者 — Phase 1</small></div>
        <div className="muted" style={{ fontSize: 12 }}>Creator</div>
        {creators.map((c) => (
          <div key={c.id} className={'creator-item' + (c.id === creatorId ? ' active' : '')} onClick={() => setCreatorId(c.id)}>
            <div>
              <div>{c.name}</div>
              <div className="meta">動画 {c.video_count ?? 0} 本{c.has_creator_dna ? ' · DNA' : ''}</div>
            </div>
            {c.id === creatorId && <button className="small ghost" onClick={(e) => { e.stopPropagation(); setModal('edit') }}>設定</button>}
          </div>
        ))}
        <button onClick={() => setModal('new')}>＋ Creator を追加</button>
        <div className="grow" />
        {status && (
          <div className="muted" style={{ fontSize: 11, lineHeight: 1.7 }}>
            <div>LLM: <span className={status.providers.llm.effective === 'mock' ? 'badge warn' : 'badge ok'}>{status.providers.llm.effective}</span> {status.providers.llm.effective !== 'mock' && status.providers.llm.model}</div>
            <div>文字起こし: <span className={status.providers.transcription.effective === 'mock' ? 'badge warn' : 'badge ok'}>{status.providers.transcription.effective}</span></div>
            <div>顔検出: <span className="badge">{status.providers.vision.effective}</span></div>
          </div>
        )}
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="steps">
            {['① 動画を入れる', '② 解析', '③ 候補を選ぶ', '④ AI編集・書き出し', '⑤ 完成'].map((s, i) => (
              <span key={s} className={i < step ? 'done' : i === step ? 'on' : ''}>{s}</span>
            ))}
          </div>
          {mock && <span className="badge warn">APIキー未設定 → モック動作（.env を設定すると本番品質）</span>}
        </div>

        {!creator ? (
          <div className="card">
            <h2>まず Creator を追加してください</h2>
            <p className="muted">YouTuber ごとに Profile（辞書・DNA・原価）を完全に分離します。</p>
            <button className="primary" onClick={() => setModal('new')}>＋ Creator を追加</button>
          </div>
        ) : (
          <>
            <DropZone creatorId={creator.id} onUploaded={(v) => { setVideos([v, ...videos]); setVideoId(v.id) }} />

            {videos.length > 0 && (
              <div className="video-list">
                {videos.map((v) => (
                  <div key={v.id} className={'video-item' + (v.id === videoId ? ' active' : '')} onClick={() => setVideoId(v.id)}>
                    <div className="t">{v.title}</div>
                    <div className="m">{fmtDur(v.duration_sec)} · {v.width}×{v.height} · {(v.size_bytes / 1e6).toFixed(0)}MB</div>
                    <div className="m">
                      <span className={'badge ' + (v.status === 'analyzed' ? 'ok' : v.status === 'failed' ? 'danger' : '')}>{STATUS_LABEL[v.status] ?? v.status}</span>{' '}
                      候補 {v.candidate_count ?? 0} · 完成 {v.export_count ?? 0}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {err && <div className="card error">{err}</div>}

            {video && (
              <div className="workspace">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <div className="card row">
                    <div className="grow">
                      <h2 style={{ fontSize: 17 }}>{video.title}</h2>
                      <div className="muted" style={{ fontSize: 12 }}>{fmtDur(video.duration_sec)} · {video.original_filename}</div>
                    </div>
                    <button className="primary" disabled={!!busy} onClick={startAnalyze}>
                      {video.status === 'analyzed' ? '🔁 再解析' : '🔍 解析してShorts候補を出す'}
                    </button>
                    <input ref={srtInputRef} type="file" accept=".srt,text/plain" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) importSrt(f); e.target.value = '' }} />
                    <button className="ghost small" disabled={!!busy} title="手持ちの SRT 字幕を文字起こしとして使う" onClick={() => srtInputRef.current?.click()}>📄 SRT を読み込む</button>
                    <button className="ghost small" disabled={!!busy} onClick={async () => { if (confirm('この動画と候補・完成品を削除しますか？')) { await api.deleteVideo(video.id); setVideoId(null); loadVideos(creator.id) } }}>削除</button>
                  </div>

                  {analyzeJob && analyzeJob.status !== 'done' && (
                    <div className="card">
                      <div className="row"><b>{analyzeJob.stage || '待機中'}</b><span className="muted">{analyzeJob.message}</span><span className="grow" /><span>{Math.round(analyzeJob.progress * 100)}%</span></div>
                      <div className="progress" style={{ marginTop: 8 }}><div style={{ width: `${analyzeJob.progress * 100}%` }} /></div>
                      {analyzeJob.status === 'failed' && <div className="error" style={{ marginTop: 8 }}>{analyzeJob.error}</div>}
                    </div>
                  )}

                  {cands.length > 0 && (
                    <>
                      <div className="row">
                        <h2 style={{ fontSize: 16 }}>Shorts 候補 {cands.length} 本</h2>
                        <span className="muted">スコア順。👍👎 は学習データとして記録されます。</span>
                        <span className="grow" />
                        <button className="small ghost" onClick={() => setSelected(new Set(cands.filter((c) => c.decision !== 'rejected').slice(0, 5).map((c) => c.id)))}>上位5本を選ぶ</button>
                        <button className="small ghost" onClick={() => setSelected(new Set())}>選択解除</button>
                      </div>
                      <div className="cand-grid">
                        {cands.map((c) => (
                          <CandidateCard
                            key={c.id}
                            c={c}
                            selected={selected.has(c.id)}
                            editing={editingId === c.id}
                            onToggle={() => toggle(c.id)}
                            onPreview={() => { const e = effective(c); setPreview({ start: e.start, end: e.end }) }}
                            onDecide={(d) => decide(c, d)}
                            onEdit={() => setEditingId(editingId === c.id ? null : c.id)}
                          />
                        ))}
                      </div>
                      <div className="exportbar">
                        <b>{selected.size} 本を選択中</b>
                        <select value={reframe} onChange={(e) => setReframe(e.target.value)} style={{ width: 'auto' }}>
                          <option value="face_track">9:16 話者追従クロップ</option>
                          <option value="center">9:16 中央クロップ</option>
                          <option value="blur_fit">全体表示＋ぼかし背景</option>
                        </select>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text)', margin: 0 }}>
                          <input type="checkbox" style={{ width: 'auto' }} checked={captions} onChange={(e) => setCaptions(e.target.checked)} /> 字幕を焼き込む
                        </label>
                        <span className="grow" />
                        <button className="primary" disabled={!selected.size || !!busy} onClick={startExport}>🎬 AI編集して一括書き出し</button>
                      </div>
                    </>
                  )}

                  {exportJob && exportJob.status !== 'done' && (
                    <div className="card">
                      <div className="row"><b>{exportJob.stage || '待機中'}</b><span className="muted">{exportJob.message}</span><span className="grow" /><span>{Math.round(exportJob.progress * 100)}%</span></div>
                      <div className="progress" style={{ marginTop: 8 }}><div style={{ width: `${exportJob.progress * 100}%` }} /></div>
                      {exportJob.status === 'failed' && <div className="error" style={{ marginTop: 8 }}>{exportJob.error}</div>}
                    </div>
                  )}

                  {exportsList.length > 0 && (
                    <div className="card">
                      <div className="row" style={{ marginBottom: 10 }}>
                        <h2 style={{ fontSize: 16 }}>完成した Shorts</h2>
                        <span className="grow" />
                        {exportsList.some((e) => e.status === 'done') && <button className="small" onClick={downloadBundle}>🗜 まとめて ZIP ダウンロード</button>}
                      </div>
                      <div className="exports">
                        {exportsList.map((e) => (
                          <div key={e.id} className="export-item">
                            <div className="row"><span className="badge">#{e.candidate_rank}</span><b style={{ fontSize: 12 }}>{e.candidate_title}</b></div>
                            {e.status === 'done' ? (
                              <>
                                <video src={`/api/exports/${e.id}/file`} controls preload="metadata" />
                                <a href={`/api/exports/${e.id}/file?download=1`} download><button className="small" style={{ width: '100%' }}>⬇ ダウンロード</button></a>
                              </>
                            ) : e.status === 'failed' ? (
                              <div className="error">{e.error}</div>
                            ) : (
                              <span className="badge">{e.status === 'rendering' ? '書き出し中…' : '待機中'}</span>
                            )}
                            <button className="small ghost" onClick={async () => { await api.deleteExport(e.id); setExports(exportsList.filter((x) => x.id !== e.id)) }}>削除</button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="preview" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div className="card">
                    <h3 style={{ fontSize: 13, marginBottom: 8 }}>プレビュー（元動画）</h3>
                    <video ref={videoRef} src={`/api/videos/${video.id}/file`} controls preload="metadata" />
                    {preview && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{fmtTime(preview.start)} → {fmtTime(preview.end)} を再生中（終点で自動停止）</div>}
                  </div>
                  {editing && (
                    <EditPanel
                      c={editing}
                      video={video}
                      videoRef={videoRef}
                      onSaved={onCandidateSaved}
                      onClose={() => setEditingId(null)}
                      onPreview={(s, e) => setPreview({ start: s, end: e })}
                      notify={notify}
                    />
                  )}
                  <CostPanel cost={cost} title="この動画の原価" />
                </div>
              </div>
            )}
          </>
        )}
      </main>

      {toast && <div className="toast" role="status">{toast}</div>}
      {modal && (
        <CreatorModal
          creator={modal === 'edit' ? creator : null}
          onClose={() => setModal(null)}
          onSaved={(c) => {
            setModal(null)
            loadCreators().then(() => setCreatorId(c.id))
          }}
        />
      )}
    </div>
  )
}
const STATUS_LABEL: Record<string, string> = { uploaded: '未解析', analyzing: '解析中', analyzed: '解析済み', failed: '失敗' }

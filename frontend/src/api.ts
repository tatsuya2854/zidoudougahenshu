// バックエンド API クライアント。APIキーはここには一切出てこない（全部サーバ側）。

export type Creator = {
  id: string
  name: string
  channel_url?: string | null
  notes?: string | null
  dictionary: DictEntry[]
  caption_style: Record<string, unknown>
  video_count?: number
  has_creator_dna?: boolean
  has_editing_dna?: boolean
}
export type DictEntry = { term: string; category: string; note?: string; reading?: string }

export type Video = {
  id: string
  creator_id: string
  title: string
  original_filename: string
  duration_sec: number
  width: number
  height: number
  status: string
  size_bytes: number
  candidate_count?: number
  export_count?: number
  created_at: string
}

export type Job = {
  id: string
  kind: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  stage: string
  progress: number
  message: string
  error?: string | null
  result?: Record<string, unknown>
}

export type CandidateData = {
  title: string
  title_alternatives: string[]
  summary: string
  hook: string
  hook_suggestion: string
  why_clip: string
  creator_likeness_reason: string
  standalone_ok: boolean
  standalone_reason: string
  retention_reason: string
  scores: Record<string, number>
  score: number
  tags: string[]
}
export type CaptionCue = { start: number; end: number; text: string } // 元動画の絶対秒
export type CropMode = 'face_track' | 'center' | 'blur_fit' | 'manual'
/** 人の手直し。無いキーは AI 案のまま。保存は PUT で部分更新（null でキー削除）。 */
export type Overrides = {
  start_sec?: number
  end_sec?: number
  title?: string
  crop_mode?: CropMode
  crop_x?: number // manual 時の左右位置 0〜1
  font_size_ratio?: number
  caption_position?: 'top' | 'center' | 'bottom'
  captions?: CaptionCue[]
}
export type Candidate = {
  id: string
  rank: number
  start_sec: number
  end_sec: number
  score: number
  title: string
  decision: 'pending' | 'accepted' | 'rejected'
  data: CandidateData
  overrides: Overrides
}
/** overrides を反映した表示用の開始/終了/タイトル。 */
export const effective = (c: Candidate) => {
  const o = c.overrides ?? {}
  return { start: o.start_sec ?? c.start_sec, end: o.end_sec ?? c.end_sec, title: o.title || c.data?.title || c.title }
}
export const isEdited = (c: Candidate) => Object.keys(c.overrides ?? {}).length > 0

export type Export = {
  id: string
  candidate_id: string
  status: 'queued' | 'rendering' | 'done' | 'failed'
  path?: string | null
  filename?: string | null
  error?: string | null
  candidate_title: string
  candidate_rank: number
  created_at: string
}

export type CostSummary = { by_category: Record<string, number>; total_usd: number; entries: number }

export type Status = {
  ok: boolean
  version: string
  providers: {
    llm: { configured: string; effective: string; model: string }
    transcription: { configured: string; effective: string; model: string }
    vision: { effective: string }
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, msg: string) {
    super(msg)
    this.status = status
  }
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status}`
    try {
      const b = await res.json()
      msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail ?? b)
    } catch {
      /* noop */
    }
    throw new ApiError(res.status, msg)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const H = { 'Content-Type': 'application/json' }

export const api = {
  status: () => fetch('/api/status').then((r) => j<Status>(r)),
  creators: () => fetch('/api/creators').then((r) => j<Creator[]>(r)),
  createCreator: (body: Partial<Creator>) => fetch('/api/creators', { method: 'POST', headers: H, body: JSON.stringify(body) }).then((r) => j<Creator>(r)),
  updateCreator: (id: string, body: Partial<Creator>) => fetch(`/api/creators/${id}`, { method: 'PUT', headers: H, body: JSON.stringify(body) }).then((r) => j<Creator>(r)),
  videos: (creatorId: string) => fetch(`/api/videos?creator_id=${creatorId}`).then((r) => j<Video[]>(r)),
  video: (id: string) => fetch(`/api/videos/${id}`).then((r) => j<Video & { latest_job: Job | null }>(r)),
  deleteVideo: (id: string) => fetch(`/api/videos/${id}`, { method: 'DELETE' }).then((r) => j<void>(r)),
  analyze: (id: string) => fetch(`/api/videos/${id}/analyze`, { method: 'POST' }).then((r) => j<Job>(r)),
  candidates: (id: string) => fetch(`/api/videos/${id}/candidates`).then((r) => j<Candidate[]>(r)),
  decide: (cid: string, decision: Candidate['decision']) =>
    fetch(`/api/candidates/${cid}/decision`, { method: 'POST', headers: H, body: JSON.stringify({ decision }) }).then((r) => j<unknown>(r)),
  // 候補の手直し（Phase5 の学習データ。サーバ側で HumanEdit に before/after が残る）
  putOverrides: (cid: string, body: Partial<Record<keyof Overrides, unknown>>) =>
    fetch(`/api/candidates/${cid}/overrides`, { method: 'PUT', headers: H, body: JSON.stringify(body) }).then((r) => j<Candidate>(r)),
  resetOverrides: (cid: string) => fetch(`/api/candidates/${cid}/reset`, { method: 'POST' }).then((r) => j<Candidate>(r)),
  candidateCaptions: (cid: string) =>
    fetch(`/api/candidates/${cid}/captions`).then((r) => j<{ source: 'auto' | 'override'; start_sec: number; end_sec: number; cues: CaptionCue[] }>(r)),
  // 以下 2 つはサーバ側が別途実装中の契約。404 のときは呼び出し側でトースト表示する
  importSrt: (videoId: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`/api/videos/${videoId}/transcript/srt`, { method: 'POST', body: fd }).then((r) => j<unknown>(r))
  },
  bundleUrl: (videoId: string) => `/api/videos/${videoId}/bundle.zip`,
  bundleAvailable: (videoId: string) => fetch(`/api/videos/${videoId}/bundle.zip`, { method: 'HEAD' }).then((r) => r.status !== 404 && r.status !== 405),
  createExports: (videoId: string, candidate_ids: string[], options: Record<string, unknown>) =>
    fetch(`/api/videos/${videoId}/exports`, { method: 'POST', headers: H, body: JSON.stringify({ candidate_ids, options }) }).then((r) => j<{ job: Job; exports: Export[] }>(r)),
  exports: (videoId: string) => fetch(`/api/videos/${videoId}/exports`).then((r) => j<Export[]>(r)),
  deleteExport: (id: string) => fetch(`/api/exports/${id}`, { method: 'DELETE' }).then((r) => j<void>(r)),
  job: (id: string) => fetch(`/api/jobs/${id}`).then((r) => j<Job>(r)),
  cancelJob: (id: string) => fetch(`/api/jobs/${id}/cancel`, { method: 'POST' }).then((r) => j<unknown>(r)),
  videoCost: (id: string) => fetch(`/api/videos/${id}/cost`).then((r) => j<CostSummary>(r)),
  creatorCost: (id: string) => fetch(`/api/costs?creator_id=${id}`).then((r) => j<CostSummary>(r)),
}

/** XHR でアップロード進捗を取る（fetch は upload progress が取れない）。 */
export function uploadVideo(creatorId: string, file: File, onProgress: (p: number) => void): Promise<Video> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/creators/${creatorId}/videos`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText))
      else {
        try {
          reject(new Error(JSON.parse(xhr.responseText).detail ?? xhr.statusText))
        } catch {
          reject(new Error(xhr.statusText))
        }
      }
    }
    xhr.onerror = () => reject(new Error('アップロードに失敗しました'))
    const fd = new FormData()
    fd.append('file', file)
    xhr.send(fd)
  })
}

export const fmtTime = (s: number) => {
  const m = Math.floor(s / 60)
  const sec = s - m * 60
  return `${m}:${sec.toFixed(1).padStart(4, '0')}`
}
export const fmtDur = (s: number) => {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}

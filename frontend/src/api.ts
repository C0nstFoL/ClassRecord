export type RecordingStatus =
  | 'uploaded'
  | 'transcribing'
  | 'transcribed'
  | 'summarizing'
  | 'completed'
  | 'failed'
  | 'recording'

export interface CurrentUser {
  id: number
  email: string | null
  name: string | null
}

export interface Recording {
  id: number
  title: string
  status: RecordingStatus
  transcript_text: string | null
  summary_text: string | null
  error_message: string | null
  is_live: boolean
  language: string
  created_at: string
  updated_at: string
}

export interface QaRecord {
  id: number
  question: string
  answer: string
  created_at: string
}

export interface SegmentSummary {
  id: number
  seq: number
  text: string
  created_at: string
}

export interface Preset {
  key: string
  label: string
}

// 录制语言：zh/en/ja/ko/yue。中文与粤语由服务器端 SenseVoice 模型识别，
// 其余语言由 Whisper 识别；安卓端本地离线识别仅支持中英双语。
export const LANGUAGES: { key: string; label: string }[] = [
  { key: 'zh', label: '中文' },
  { key: 'en', label: 'English' },
  { key: 'ja', label: '日本語' },
  { key: 'ko', label: '한국어' },
  { key: 'yue', label: '粤语' },
]

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    credentials: 'include',
    ...init,
  })
  if (!resp.ok) {
    if (resp.status === 401) {
      throw new UnauthorizedError()
    }
    const text = await resp.text()
    throw new Error(text || `请求失败：${resp.status}`)
  }
  return resp.json() as Promise<T>
}

export class UnauthorizedError extends Error {
  constructor() {
    super('未登录')
  }
}

export const api = {
  me: () => request<CurrentUser>('/auth/me'),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  listRecordings: () => request<Recording[]>('/api/recordings'),
  getRecording: (id: number) => request<Recording>(`/api/recordings/${id}`),
  deleteRecording: (id: number) =>
    request<{ ok: boolean }>(`/api/recordings/${id}`, { method: 'DELETE' }),
  retryRecording: (id: number) =>
    request<Recording>(`/api/recordings/${id}/retry`, { method: 'POST' }),
  askQuestion: (id: number, question: string) =>
    request<QaRecord>(`/api/recordings/${id}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    }),
  listQa: (id: number) => request<QaRecord[]>(`/api/recordings/${id}/qa`),
  listPresets: () => request<Preset[]>('/api/recordings/presets'),
  uploadRecording: (file: Blob, title: string, filename: string, preset: string, language: string) => {
    const form = new FormData()
    form.append('file', file, filename)
    form.append('title', title)
    form.append('preset', preset)
    form.append('language', language)
    return request<Recording>('/api/recordings', { method: 'POST', body: form })
  },
  listSegments: (id: number) => request<SegmentSummary[]>(`/api/recordings/${id}/segments`),
  createLiveRecording: (title: string, language: string) => {
    const form = new FormData()
    form.append('title', title)
    form.append('language', language)
    return request<Recording>('/api/recordings/live', { method: 'POST', body: form })
  },
  liveStreamUrl: (id: number, preset: string) => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${protocol}://${window.location.host}/api/recordings/ws/${id}/stream?preset=${encodeURIComponent(preset)}`
  },
}

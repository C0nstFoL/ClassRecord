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
  is_paused: boolean
  auto_summary: boolean
  language: string
  share_expires_at: string | null
  created_at: string
  updated_at: string
}

export interface SharedRecording {
  title: string
  language: string
  transcript_text: string | null
  summary_text: string | null
  segments: { seq: number; text: string }[]
  expires_at: string
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
    // FastAPI 错误响应为 {"detail": "..."}，尽量提取可读信息
    let message = `请求失败：${resp.status}`
    try {
      const data = await resp.json()
      if (typeof data?.detail === 'string') message = data.detail
    } catch {
      /* 保留默认错误信息 */
    }
    throw new Error(message)
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
  renameRecording: (id: number, title: string) =>
    request<Recording>(`/api/recordings/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  createShareLink: (id: number, hours: number) =>
    request<{ token: string; expires_at: string }>(`/api/recordings/${id}/share`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hours }),
    }),
  revokeShareLink: (id: number) =>
    request<Recording>(`/api/recordings/${id}/share`, { method: 'DELETE' }),
  // 免登录的公开接口，token 即凭据
  getSharedRecording: (token: string) => request<SharedRecording>(`/api/share/${token}`),
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
  // 为已有转写的记录手动生成（或重新生成）总结
  summarizeRecording: (id: number) =>
    request<Recording>(`/api/recordings/${id}/summarize`, { method: 'POST' }),
  uploadRecording: (file: Blob, title: string, filename: string, preset: string, language: string, autoSummary: boolean) => {
    const form = new FormData()
    form.append('file', file, filename)
    form.append('title', title)
    form.append('preset', preset)
    form.append('language', language)
    form.append('auto_summary', String(autoSummary))
    return request<Recording>('/api/recordings', { method: 'POST', body: form })
  },
  listSegments: (id: number) => request<SegmentSummary[]>(`/api/recordings/${id}/segments`),
  // 结束一条暂停中的实时录制并生成总结
  finishRecording: (id: number) =>
    request<Recording>(`/api/recordings/${id}/finish`, { method: 'POST' }),
  // 把 source_ids 合并进 mainId（按时间拼接转写后重新总结，来源记录被删除）
  mergeRecordings: (mainId: number, sourceIds: number[]) =>
    request<Recording>(`/api/recordings/${mainId}/merge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_ids: sourceIds }),
    }),
  createLiveRecording: (title: string, language: string, autoSummary: boolean) => {
    const form = new FormData()
    form.append('title', title)
    form.append('language', language)
    form.append('auto_summary', String(autoSummary))
    return request<Recording>('/api/recordings/live', { method: 'POST', body: form })
  },
  liveStreamUrl: (id: number, preset: string) => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${protocol}://${window.location.host}/api/recordings/ws/${id}/stream?preset=${encodeURIComponent(preset)}`
  },
}

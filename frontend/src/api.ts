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
  uploadRecording: (file: Blob, title: string, filename: string) => {
    const form = new FormData()
    form.append('file', file, filename)
    form.append('title', title)
    return request<Recording>('/api/recordings', { method: 'POST', body: form })
  },
  listSegments: (id: number) => request<SegmentSummary[]>(`/api/recordings/${id}/segments`),
  createLiveRecording: (title: string) => {
    const form = new FormData()
    form.append('title', title)
    return request<Recording>('/api/recordings/live', { method: 'POST', body: form })
  },
  liveStreamUrl: (id: number) => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${protocol}://${window.location.host}/api/recordings/ws/${id}/stream`
  },
}

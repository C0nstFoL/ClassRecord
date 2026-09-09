import { useState } from 'react'
import type { Recording } from '../api'

const STATUS_LABEL: Record<Recording['status'], string> = {
  uploaded: '已上传',
  transcribing: '转写中',
  transcribed: '已转写',
  summarizing: '总结中',
  completed: '已完成',
  failed: '处理失败',
  recording: '实时录制中',
}

interface Props {
  recordings: Recording[]
  selectedId: number | null
  onSelect: (id: number) => void
  onDelete: (id: number) => void
}

export default function RecordingList({ recordings, selectedId, onSelect, onDelete }: Props) {
  const [keyword, setKeyword] = useState('')

  if (recordings.length === 0) {
    return <p className="hint">还没有课堂记录，上传一段录音开始吧</p>
  }

  const kw = keyword.trim().toLowerCase()
  const filtered = kw ? recordings.filter((r) => r.title.toLowerCase().includes(kw)) : recordings

  return (
    <div>
      {recordings.length >= 3 && (
        <input
          className="input list-search"
          placeholder="搜索课堂标题..."
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
      )}
      {filtered.length === 0 ? (
        <p className="hint">没有匹配「{keyword}」的记录</p>
      ) : (
        <ul className="list">
          {filtered.map((r) => (
            <li
              key={r.id}
              className={`list-item ${r.id === selectedId ? 'active' : ''}`}
              onClick={() => onSelect(r.id)}
            >
              <div className="list-item-title">{r.title}</div>
              <div className="list-item-meta">
                <span className={`status status-${r.status}`}>{STATUS_LABEL[r.status]}</span>
                <span>{new Date(r.created_at).toLocaleString()}</span>
              </div>
              <button
                className="btn small danger"
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(r.id)
                }}
              >
                删除
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

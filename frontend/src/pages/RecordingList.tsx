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
  onMerge: (ids: number[]) => void
}

export default function RecordingList({ recordings, selectedId, onSelect, onDelete, onMerge }: Props) {
  const [keyword, setKeyword] = useState('')
  // 多选合并模式
  const [selectMode, setSelectMode] = useState(false)
  const [checked, setChecked] = useState<number[]>([])

  if (recordings.length === 0) {
    return <p className="hint">还没有课堂记录，上传一段录音开始吧</p>
  }

  const kw = keyword.trim().toLowerCase()
  const filtered = kw ? recordings.filter((r) => r.title.toLowerCase().includes(kw)) : recordings

  const toggleChecked = (id: number) => {
    setChecked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setChecked([])
  }

  const recordingIds = recordings.filter((r) => r.status === 'recording').map((r) => r.id)
  const mergeable = checked.filter((id) => !recordingIds.includes(id))
  const canMerge = mergeable.length >= 2

  return (
    <div>
      <div className="list-toolbar">
        {recordings.length >= 3 && (
          <input
            className="input list-search"
            placeholder="搜索课堂标题..."
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
        )}
        <button
          className="btn small"
          onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
        >
          {selectMode ? '取消选择' : '选择合并'}
        </button>
      </div>

      {selectMode && (
        <div className="merge-bar">
          <span className="hint">勾选 2 条及以上可合并（按时间顺序拼接，保留最早的一条）</span>
          <button
            className="btn small primary"
            disabled={!canMerge}
            onClick={() => {
              onMerge(mergeable)
              exitSelectMode()
            }}
          >
            合并所选{canMerge ? `（${mergeable.length} 条）` : ''}
          </button>
        </div>
      )}

      {filtered.length === 0 ? (
        <p className="hint">没有匹配「{keyword}」的记录</p>
      ) : (
        <ul className="list">
          {filtered.map((r) => (
            <li
              key={r.id}
              className={`list-item ${r.id === selectedId && !selectMode ? 'active' : ''} ${selectMode && checked.includes(r.id) ? 'checked' : ''}`}
              onClick={() => (selectMode ? toggleChecked(r.id) : onSelect(r.id))}
            >
              <div className="list-item-title">
                {selectMode && (
                  <input
                    type="checkbox"
                    checked={checked.includes(r.id)}
                    onChange={() => toggleChecked(r.id)}
                    onClick={(e) => e.stopPropagation()}
                  />
                )}
                {r.title}
              </div>
              <div className="list-item-meta">
                <span className={`status status-${r.status}`}>
                  {r.status === 'recording' && r.is_paused ? '已暂停' : STATUS_LABEL[r.status]}
                </span>
                <span>{new Date(r.created_at).toLocaleString()}</span>
              </div>
              {!selectMode && (
                <button
                  className="btn small danger"
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(r.id)
                  }}
                >
                  删除
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

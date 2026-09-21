import { useState } from 'react'
import { api, type Recording } from '../api'

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
  onHomeworkCreated: (id: number) => void
}

export default function RecordingList({ recordings, selectedId, onSelect, onDelete, onMerge, onHomeworkCreated }: Props) {
  const [keyword, setKeyword] = useState('')
  // 多选合并模式
  const [selectMode, setSelectMode] = useState(false)
  const [checked, setChecked] = useState<number[]>([])
  const [homeworkError, setHomeworkError] = useState<string | null>(null)
  const [extractingHomework, setExtractingHomework] = useState(false)
  const [expandedHomeworkIds, setExpandedHomeworkIds] = useState<number[]>([])
  const [relatedRecordingIds, setRelatedRecordingIds] = useState<Record<number, number[]>>({})
  const [loadingRelatedIds, setLoadingRelatedIds] = useState<number[]>([])
  const [relatedErrors, setRelatedErrors] = useState<Record<number, string>>({})

  if (recordings.length === 0) {
    return <p className="hint">还没有课堂记录，上传一段录音开始吧</p>
  }

  const kw = keyword.trim().toLowerCase()
  const filtered = kw ? recordings.filter((r) => r.title.toLowerCase().includes(kw)) : recordings
  const recordingIds = recordings.filter((r) => r.status === 'recording').map((r) => r.id)
  const mergeable = checked.filter((id) => {
    const recording = recordings.find((item) => item.id === id)
    return recording?.record_type === 'class' && !recordingIds.includes(id)
  })
  const canMerge = mergeable.length >= 2
  const homeworkEligibleIds = new Set(
    recordings
      .filter((r) => r.record_type === 'class' && r.status !== 'recording' && Boolean(r.summary_text?.trim()))
      .map((r) => r.id),
  )
  const homeworkSelection = checked.filter((id) => homeworkEligibleIds.has(id))
  const canExtractHomework = homeworkSelection.length >= 2

  const toggleChecked = (id: number) => {
    setChecked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setChecked([])
  }

  const toggleRelatedRecordings = async (homeworkId: number) => {
    if (expandedHomeworkIds.includes(homeworkId)) {
      setExpandedHomeworkIds((ids) => ids.filter((id) => id !== homeworkId))
      return
    }
    setExpandedHomeworkIds((ids) => [...ids, homeworkId])
    if (relatedRecordingIds[homeworkId] || loadingRelatedIds.includes(homeworkId)) return
    setLoadingRelatedIds((ids) => [...ids, homeworkId])
    setRelatedErrors((errors) => ({ ...errors, [homeworkId]: '' }))
    try {
      const tasks = await api.listHomeworkTasks(homeworkId)
      const sourceIds = Array.from(
        new Set(tasks.flatMap((task) => task.sources.map((source) => source.id))),
      )
      setRelatedRecordingIds((items) => ({ ...items, [homeworkId]: sourceIds }))
    } catch (err) {
      setRelatedErrors((errors) => ({
        ...errors,
        [homeworkId]: err instanceof Error ? err.message : '加载相关课程失败',
      }))
    } finally {
      setLoadingRelatedIds((ids) => ids.filter((id) => id !== homeworkId))
    }
  }

  const handleExtractHomework = async () => {
    if (!canExtractHomework || extractingHomework) return
    setExtractingHomework(true)
    setHomeworkError(null)
    try {
      const job = await api.extractHomework(homeworkSelection)
      let result = job
      while (result.status === 'pending' || result.status === 'processing') {
        await new Promise((resolve) => window.setTimeout(resolve, 2000))
        result = await api.getHomeworkJob(job.job_id)
      }
      if (result.status === 'failed' || result.result_recording_id === null) {
        throw new Error(result.error || '整理待办与作业失败')
      }
      onHomeworkCreated(result.result_recording_id)
      exitSelectMode()
    } catch (err) {
      setHomeworkError(err instanceof Error ? err.message : '整理待办与作业失败')
    } finally {
      setExtractingHomework(false)
    }
  }

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
          {selectMode ? '取消选择' : '选择记录'}
        </button>
      </div>

      {selectMode && (
        <div className="merge-bar">
          <span className="hint">选择 2 条及以上记录后，可合并；有课堂总结的记录还可整理待办与作业</span>
          <div className="selection-actions">
            <button
              className="btn small"
              disabled={!canMerge}
              onClick={() => {
                onMerge(mergeable)
                exitSelectMode()
              }}
            >
              合并所选{canMerge ? `（${mergeable.length} 条）` : ''}
            </button>
            <button
              className="btn small primary"
              disabled={!canExtractHomework || extractingHomework}
              onClick={handleExtractHomework}
            >
              {extractingHomework ? '整理中...' : `整理待办与作业${canExtractHomework ? `（${homeworkSelection.length} 条）` : ''}`}
            </button>
          </div>
        </div>
      )}

      {homeworkError && <p className="error">整理待办与作业失败：{homeworkError}</p>}

      {filtered.length === 0 ? (
        <p className="hint">没有匹配「{keyword}」的记录</p>
      ) : (
        <ul className="list">
          {filtered.map((r) => {
            const homeworkEligible = homeworkEligibleIds.has(r.id)
            const selectable = r.record_type === 'class'
            const relatedIds = relatedRecordingIds[r.id] ?? []
            const relatedRecordings = relatedIds
              .map((id) => recordings.find((recording) => recording.id === id))
              .filter((recording): recording is Recording => Boolean(recording))
            const relatedExpanded = expandedHomeworkIds.includes(r.id)
            return (
              <li
                key={r.id}
                className={`list-item ${r.id === selectedId && !selectMode ? 'active' : ''} ${selectMode && checked.includes(r.id) ? 'checked' : ''}`}
                onClick={() => (selectMode ? selectable && toggleChecked(r.id) : onSelect(r.id))}
              >
                <div className="list-item-title">
                  {selectMode && selectable && (
                    <input
                      type="checkbox"
                      checked={checked.includes(r.id)}
                      onChange={() => toggleChecked(r.id)}
                      onClick={(e) => e.stopPropagation()}
                      aria-label={`选择${r.title}`}
                    />
                  )}
                  {r.title}
                  {r.record_type === 'homework' && <span className="record-tag">待办作业</span>}
                  {selectMode && selectable && !homeworkEligible && (
                    <span className="hint" title="该记录仍可用于合并，但还没有可供整理的课堂总结">
                      暂无课堂总结
                    </span>
                  )}
                </div>
                <div className="list-item-meta">
                  <span className={`status status-${r.status}`}>
                    {r.status === 'recording' && r.is_paused ? '已暂停' : STATUS_LABEL[r.status]}
                  </span>
                  <span>{new Date(r.created_at).toLocaleString()}</span>
                </div>
                {r.record_type === 'homework' && !selectMode && (
                  <div className="homework-related" onClick={(event) => event.stopPropagation()}>
                    <button
                      type="button"
                      className="homework-related-toggle"
                      aria-expanded={relatedExpanded}
                      onClick={() => toggleRelatedRecordings(r.id)}
                    >
                      <span className={`homework-related-arrow ${relatedExpanded ? 'expanded' : ''}`}>▶</span>
                      相关课程
                      {relatedRecordingIds[r.id] && `（${relatedRecordings.length}）`}
                    </button>
                    {relatedExpanded && (
                      <div className="homework-related-list">
                        {loadingRelatedIds.includes(r.id) && <span className="hint">加载中...</span>}
                        {relatedErrors[r.id] && <span className="error">{relatedErrors[r.id]}</span>}
                        {!loadingRelatedIds.includes(r.id) && !relatedErrors[r.id] && relatedRecordings.length === 0 && (
                          <span className="hint">没有可用的相关课程记录</span>
                        )}
                        {relatedRecordings.map((related) => (
                          <button
                            type="button"
                            className="homework-related-item"
                            key={related.id}
                            onClick={() => onSelect(related.id)}
                          >
                            <span>{related.title}</span>
                            <span className="homework-related-date">
                              {new Date(related.created_at).toLocaleDateString()}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
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
            )
          })}
        </ul>
      )}
    </div>
  )
}

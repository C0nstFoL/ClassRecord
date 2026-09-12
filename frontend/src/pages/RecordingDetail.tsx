import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type QaRecord, type Recording, type SegmentSummary } from '../api'

interface Props {
  recording: Recording | null
  onRetried?: () => void
  onChanged?: () => void
}

type Tab = 'summary' | 'transcript' | 'segments' | 'ask'

export default function RecordingDetail({ recording, onRetried, onChanged }: Props) {
  const [tab, setTab] = useState<Tab>('summary')
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [askError, setAskError] = useState<string | null>(null)
  const [qaHistory, setQaHistory] = useState<QaRecord[]>([])
  const [qaLoading, setQaLoading] = useState(false)
  const [segments, setSegments] = useState<SegmentSummary[]>([])
  // 重命名
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [titleBusy, setTitleBusy] = useState(false)
  const [titleError, setTitleError] = useState<string | null>(null)
  // 分享链接
  const [showShare, setShowShare] = useState(false)
  const [shareHours, setShareHours] = useState(24)
  const [shareLink, setShareLink] = useState<string | null>(null)
  const [shareExpires, setShareExpires] = useState<Date | null>(null)
  const [shareBusy, setShareBusy] = useState(false)
  const [shareError, setShareError] = useState<string | null>(null)

  useEffect(() => {
    if (recording?.summary_text) {
      setTab('summary')
    } else if (recording?.is_live) {
      setTab('segments')
    } else if (recording?.transcript_text) {
      setTab('transcript')
    }
    setQuestion('')
    setAskError(null)
    setQaHistory([])
    setSegments([])
    setRetryError(null)
    setCopied(null)
    setEditingTitle(false)
    setTitleError(null)
    setShowShare(false)
    setShareLink(null)
    // 刷新页面后 token 不会回传（安全考虑），但需要从后端已有的过期时间
    // 恢复"分享中"状态，否则撤销按钮不会出现
    const expiresAt = recording?.share_expires_at ? new Date(recording.share_expires_at) : null
    setShareExpires(expiresAt && expiresAt > new Date() ? expiresAt : null)
    setShareError(null)
  }, [recording?.id, recording?.share_expires_at])

  useEffect(() => {
    if (!recording || !recording.transcript_text) return
    setQaLoading(true)
    api
      .listQa(recording.id)
      .then((items) => setQaHistory([...items].reverse()))
      .catch(() => {
        /* 忽略加载历史失败，不影响继续提问 */
      })
      .finally(() => setQaLoading(false))
  }, [recording?.id])

  useEffect(() => {
    if (!recording || !recording.is_live) return
    let cancelled = false
    const load = () => {
      api
        .listSegments(recording.id)
        .then((items) => {
          if (!cancelled) setSegments(items)
        })
        .catch(() => {
          /* 忽略加载分段小结失败 */
        })
    }
    load()
    const timer =
      recording.status === 'recording' ? window.setInterval(load, 5000) : undefined
    return () => {
      cancelled = true
      if (timer) window.clearInterval(timer)
    }
  }, [recording?.id, recording?.status])

  if (!recording) {
    return <div className="card">选择左侧的一条课堂记录查看详情</div>
  }

  const hasSummary = Boolean(recording.summary_text)
  const hasTranscript = Boolean(recording.transcript_text)
  const hasSegments = segments.length > 0

  const copyText = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(key)
      window.setTimeout(() => setCopied((prev) => (prev === key ? null : prev)), 2000)
    } catch {
      setCopied(null)
    }
  }

  const exportMarkdown = () => {
    const parts = [`# ${recording.title}`, '']
    if (recording.summary_text) {
      parts.push('## ✨ 课堂总结', '', recording.summary_text, '')
    }
    if (segments.length > 0) {
      parts.push('## 🧩 分段小结', '')
      segments.forEach((seg) => {
        parts.push(seg.text, '')
      })
    }
    if (recording.transcript_text) {
      parts.push('## 📄 转写原文', '', recording.transcript_text, '')
    }
    const blob = new Blob([parts.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${recording.title}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleRetry = async () => {
    setRetrying(true)
    setRetryError(null)
    try {
      await api.retryRecording(recording.id)
      onRetried?.()
    } catch (err) {
      setRetryError(err instanceof Error ? err.message : '重试失败')
    } finally {
      setRetrying(false)
    }
  }

  const handleAsk = async () => {
    const q = question.trim()
    if (!q || asking) return
    setAsking(true)
    setAskError(null)
    try {
      const qa = await api.askQuestion(recording.id, q)
      setQaHistory((prev) => [qa, ...prev])
      setQuestion('')
    } catch (err) {
      setAskError(err instanceof Error ? err.message : '提问失败')
    } finally {
      setAsking(false)
    }
  }

  const startEditTitle = () => {
    setTitleDraft(recording.title)
    setEditingTitle(true)
    setTitleError(null)
  }

  const handleRename = async () => {
    const title = titleDraft.trim()
    if (!title || titleBusy) return
    setTitleBusy(true)
    setTitleError(null)
    try {
      await api.renameRecording(recording.id, title)
      setEditingTitle(false)
      onChanged?.()
    } catch (err) {
      setTitleError(err instanceof Error ? err.message : '重命名失败')
    } finally {
      setTitleBusy(false)
    }
  }

  const handleCreateShare = async () => {
    if (shareBusy) return
    setShareBusy(true)
    setShareError(null)
    try {
      const res = await api.createShareLink(recording.id, shareHours)
      setShareLink(`${window.location.origin}/s/${res.token}`)
      setShareExpires(new Date(res.expires_at))
      onChanged?.()
    } catch (err) {
      setShareError(err instanceof Error ? err.message : '生成分享链接失败')
    } finally {
      setShareBusy(false)
    }
  }

  const handleRevokeShare = async () => {
    if (shareBusy) return
    setShareBusy(true)
    setShareError(null)
    try {
      await api.revokeShareLink(recording.id)
      setShareLink(null)
      setShareExpires(null)
      onChanged?.()
    } catch (err) {
      setShareError(err instanceof Error ? err.message : '撤销分享失败')
    } finally {
      setShareBusy(false)
    }
  }

  return (
    // key 随录音 id 变化，切换记录时整卡重挂载以重放入场动画
    <div className="card detail-card" key={recording.id}>
      {editingTitle ? (
        <div className="rename-row">
          <input
            type="text"
            className="ask-input"
            value={titleDraft}
            autoFocus
            onChange={(e) => setTitleDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                handleRename()
              } else if (e.key === 'Escape') {
                setEditingTitle(false)
              }
            }}
            disabled={titleBusy}
          />
          <button className="btn small" onClick={handleRename} disabled={titleBusy || !titleDraft.trim()}>
            {titleBusy ? '保存中...' : '保存'}
          </button>
          <button className="btn small" onClick={() => setEditingTitle(false)} disabled={titleBusy}>
            取消
          </button>
        </div>
      ) : (
        <div className="title-row">
          <h2>{recording.title}</h2>
          <button
            className="btn small icon-btn"
            title="重命名"
            onClick={startEditTitle}
            disabled={recording.status === 'recording'}
          >
            ✏️
          </button>
        </div>
      )}
      {titleError && <p className="error">{titleError}</p>}

      {(hasSummary || hasTranscript) && (
        <div className="detail-actions">
          <button
            className="btn small"
            onClick={() =>
              copyText(
                'all',
                [recording.summary_text, recording.transcript_text]
                  .filter(Boolean)
                  .join('\n\n'),
              )
            }
          >
            {copied === 'all' ? '✓ 已复制' : '复制内容'}
          </button>
          <button className="btn small" onClick={exportMarkdown}>
            导出 Markdown
          </button>
          <button
            className="btn small"
            onClick={() => {
              setShowShare((v) => !v)
              setShareError(null)
            }}
          >
            {showShare ? '收起分享' : '🔗 分享'}
          </button>
        </div>
      )}

      {showShare && (
        <div className="share-panel">
          {shareExpires && (
            <p className="hint share-active-hint">
              链接分享中，有效期至{' '}
              {shareExpires.toLocaleString('zh-CN', { hour12: false })}
            </p>
          )}
          {shareLink && (
            <div className="share-link-row">
              <input type="text" className="ask-input" readOnly value={shareLink} onFocus={(e) => e.target.select()} />
              <button className="btn small" onClick={() => copyText('share', shareLink)}>
                {copied === 'share' ? '✓ 已复制' : '复制链接'}
              </button>
            </div>
          )}
          <div className="share-controls">
            <select
              className="ask-input share-hours"
              value={shareHours}
              onChange={(e) => setShareHours(Number(e.target.value))}
            >
              <option value={24}>24 小时</option>
              <option value={72}>3 天</option>
              <option value={168}>7 天</option>
            </select>
            <button className="btn small" onClick={handleCreateShare} disabled={shareBusy}>
              {shareBusy ? '处理中...' : shareExpires ? '重新生成链接' : '生成分享链接'}
            </button>
            {shareExpires && (
              <button className="btn small danger" onClick={handleRevokeShare} disabled={shareBusy}>
                撤销分享
              </button>
            )}
          </div>
          <p className="hint">访问链接的人无需登录即可查看本记录的总结与转写内容</p>
          {shareError && <p className="error">{shareError}</p>}
        </div>
      )}

      {recording.status === 'failed' && (
        <div className="error-box">
          <p className="error">处理失败：{recording.error_message}</p>
          {retryError && <p className="error">重试失败：{retryError}</p>}
          {hasTranscript && (
            <button className="btn small" onClick={handleRetry} disabled={retrying}>
              {retrying ? '重试中...' : '重试总结'}
            </button>
          )}
        </div>
      )}
      {recording.status === 'recording' && (
        <div className="recording-status">
          <span className="recording-dot" />
          <span className="hint">
            {window.sessionStorage.getItem(`live-paused-${recording.id}`)
              ? '录制已暂停'
              : window.sessionStorage.getItem(`live-recording-${recording.id}`)
                ? '正在实时录制与转写...'
                : '正在由其他设备实时录制与转写...'}
          </span>
        </div>
      )}
      {(recording.status === 'uploaded' || recording.status === 'transcribing') && (
        <p className="hint">正在转写语音，请稍候...</p>
      )}
      {recording.status === 'summarizing' && <p className="hint">转写完成，正在生成总结...</p>}

      {(hasSummary || hasTranscript || hasSegments) && (
        <div className="detail-tabs">
          {hasSummary && (
            <button
              className={`detail-tab ${tab === 'summary' ? 'active' : ''}`}
              onClick={() => setTab('summary')}
            >
              ✨ 课堂总结
            </button>
          )}
          {hasSegments && (
            <button
              className={`detail-tab ${tab === 'segments' ? 'active' : ''}`}
              onClick={() => setTab('segments')}
            >
              🧩 分段小结
            </button>
          )}
          {hasTranscript && (
            <button
              className={`detail-tab ${tab === 'transcript' ? 'active' : ''}`}
              onClick={() => setTab('transcript')}
            >
              📄 转写原文
            </button>
          )}
          {hasTranscript && (
            <button
              className={`detail-tab ${tab === 'ask' ? 'active' : ''}`}
              onClick={() => setTab('ask')}
            >
              💬 提问
            </button>
          )}
        </div>
      )}

      {tab === 'summary' && hasSummary && (
        <div className="tab-with-action">
          <div className="markdown-body summary-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{recording.summary_text}</ReactMarkdown>
          </div>
          <button className="btn small copy-btn" onClick={() => copyText('summary', recording.summary_text ?? '')}>
            {copied === 'summary' ? '✓ 已复制' : '复制'}
          </button>
        </div>
      )}

      {tab === 'segments' && hasSegments && (
        <div className="live-segments">
          {segments.map((seg) => (
            <div key={seg.id} className="live-segment-item">
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{seg.text}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'transcript' && hasTranscript && (
        <div className="tab-with-action">
          <p className="text-block">{recording.transcript_text}</p>
          <button
            className="btn small copy-btn"
            onClick={() => copyText('transcript', recording.transcript_text ?? '')}
          >
            {copied === 'transcript' ? '✓ 已复制' : '复制'}
          </button>
        </div>
      )}

      {tab === 'ask' && hasTranscript && (
        <div className="ask-panel">
          <div className="ask-history">
            {qaLoading && <p className="hint">加载历史提问...</p>}
            {!qaLoading && qaHistory.length === 0 && (
              <p className="hint">针对本节课的转写内容提出你的问题，例如"这节课讲了哪些知识点？"</p>
            )}
            {qaHistory.map((item) => (
              <div key={item.id} className="qa-item">
                <div className="qa-question">Q：{item.question}</div>
                <div className="markdown-body qa-answer">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.answer}</ReactMarkdown>
                </div>
              </div>
            ))}
          </div>
          {askError && <p className="error">{askError}</p>}
          <div className="ask-input-row">
            <input
              type="text"
              className="ask-input"
              placeholder="输入你的问题..."
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleAsk()
                }
              }}
              disabled={asking}
            />
            <button className="btn small" onClick={handleAsk} disabled={asking || !question.trim()}>
              {asking ? '提问中...' : '提问'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

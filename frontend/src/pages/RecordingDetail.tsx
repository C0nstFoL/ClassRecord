import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type QaRecord, type Recording, type SegmentSummary } from '../api'

interface Props {
  recording: Recording | null
  onRetried?: () => void
}

type Tab = 'summary' | 'transcript' | 'segments' | 'ask'

export default function RecordingDetail({ recording, onRetried }: Props) {
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
  }, [recording?.id])

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

  return (
    // key 随录音 id 变化，切换记录时整卡重挂载以重放入场动画
    <div className="card detail-card" key={recording.id}>
      <h2>{recording.title}</h2>

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
            {window.sessionStorage.getItem(`live-recording-${recording.id}`)
              ? '正在实时录制与转写...'
              : '连接已断开，正在自动收尾生成总结...'}
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

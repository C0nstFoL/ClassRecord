import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type QaRecord, type Recording } from '../api'

interface Props {
  recording: Recording | null
  onRetried?: () => void
}

type Tab = 'summary' | 'transcript' | 'ask'

export default function RecordingDetail({ recording, onRetried }: Props) {
  const [tab, setTab] = useState<Tab>('summary')
  const [retrying, setRetrying] = useState(false)
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [askError, setAskError] = useState<string | null>(null)
  const [qaHistory, setQaHistory] = useState<QaRecord[]>([])
  const [qaLoading, setQaLoading] = useState(false)

  useEffect(() => {
    if (recording?.summary_text) {
      setTab('summary')
    } else if (recording?.transcript_text) {
      setTab('transcript')
    }
    setQuestion('')
    setAskError(null)
    setQaHistory([])
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

  if (!recording) {
    return <div className="card">选择左侧的一条课堂记录查看详情</div>
  }

  const hasSummary = Boolean(recording.summary_text)
  const hasTranscript = Boolean(recording.transcript_text)

  const handleRetry = async () => {
    setRetrying(true)
    try {
      await api.retryRecording(recording.id)
      onRetried?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : '重试失败')
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
    <div className="card detail-card">
      <h2>{recording.title}</h2>

      {recording.status === 'failed' && (
        <div className="error-box">
          <p className="error">处理失败：{recording.error_message}</p>
          {hasTranscript && (
            <button className="btn small" onClick={handleRetry} disabled={retrying}>
              {retrying ? '重试中...' : '重试总结'}
            </button>
          )}
        </div>
      )}
      {(recording.status === 'uploaded' || recording.status === 'transcribing') && (
        <p className="hint">正在转写语音，请稍候...</p>
      )}
      {recording.status === 'summarizing' && <p className="hint">转写完成，正在生成总结...</p>}

      {(hasSummary || hasTranscript) && (
        <div className="detail-tabs">
          {hasSummary && (
            <button
              className={`detail-tab ${tab === 'summary' ? 'active' : ''}`}
              onClick={() => setTab('summary')}
            >
              ✨ 课堂总结
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
        <div className="markdown-body summary-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{recording.summary_text}</ReactMarkdown>
        </div>
      )}

      {tab === 'transcript' && hasTranscript && (
        <p className="text-block">{recording.transcript_text}</p>
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

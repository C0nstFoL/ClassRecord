import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type SharedRecording } from '../api'

/**
 * 免登录分享页：访问 /s/{token} 时由 App 路由到此组件，
 * 凭 URL 中的 token 从公开接口拉取只读内容。
 */
export default function SharePage({ token }: { token: string }) {
  const [data, setData] = useState<SharedRecording | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'summary' | 'segments' | 'transcript'>('summary')

  useEffect(() => {
    api
      .getSharedRecording(token)
      .then((rec) => {
        setData(rec)
        if (!rec.summary_text && rec.segments.length > 0) setTab('segments')
        else if (!rec.summary_text && rec.transcript_text) setTab('transcript')
      })
      .catch((err) => setError(err instanceof Error ? err.message : '加载失败'))
  }, [token])

  if (error) {
    return (
      <div className="share-page">
        <div className="card share-card">
          <h1>课堂记录分享</h1>
          <p className="error">{error}</p>
          <p className="hint">链接可能已过期或被分享者撤销</p>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="share-page">
        <div className="card share-card">
          <p className="hint">加载中...</p>
        </div>
      </div>
    )
  }

  const hasSummary = Boolean(data.summary_text)
  const hasSegments = data.segments.length > 0
  const hasTranscript = Boolean(data.transcript_text)
  const expires = new Date(data.expires_at)

  return (
    <div className="share-page">
      <div className="card share-card">
        <h1>{data.title}</h1>
        <p className="hint">
          课堂记录分享 · 有效期至 {expires.toLocaleString('zh-CN', { hour12: false })}
        </p>

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
        </div>

        {tab === 'summary' && hasSummary && (
          <div className="markdown-body summary-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.summary_text}</ReactMarkdown>
          </div>
        )}
        {tab === 'segments' && hasSegments && (
          <div className="live-segments">
            {data.segments.map((seg) => (
              <div key={seg.seq} className="live-segment-item">
                <div className="markdown-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{seg.text}</ReactMarkdown>
                </div>
              </div>
            ))}
          </div>
        )}
        {tab === 'transcript' && hasTranscript && (
          <p className="text-block">{data.transcript_text}</p>
        )}

        <p className="share-footer">由 课堂记录助手 生成 · 内容为只读分享</p>
      </div>
    </div>
  )
}

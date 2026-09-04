import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { startRecordingKeepAlive, stopRecordingKeepAlive, updateRecordingNotification } from '../nativeRecording'

interface Props {
  onStarted: () => void
  onFinished: () => void
}

type DownMessage =
  | { type: 'transcript_delta'; text: string }
  | { type: 'segment_summary'; seq: number; text: string }
  | { type: 'error'; message: string }
  | { type: 'done' }

function formatDuration(totalSeconds: number) {
  const m = Math.floor(totalSeconds / 60)
  const s = totalSeconds % 60
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

/**
 * 实时流式录制：通过 WebSocket 持续推送 MediaRecorder 音频块到后端，
 * 后端增量转写后推回文本，并在达到分段阈值时推回一段小结。
 */
export default function LiveRecorder({ onStarted, onFinished }: Props) {
  const [title, setTitle] = useState('')
  const [isLive, setIsLive] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [transcript, setTranscript] = useState('')
  const [segments, setSegments] = useState<{ seq: number; text: string }[]>([])
  const [error, setError] = useState('')

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<number | null>(null)
  const heartbeatRef = useRef<number | null>(null)
  const elapsedRef = useRef(0)
  const snippetRef = useRef('')
  const transcriptEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
      if (heartbeatRef.current) window.clearInterval(heartbeatRef.current)
      mediaRecorderRef.current?.stop()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      wsRef.current?.close()
      void stopRecordingKeepAlive()
    }
  }, [])

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [transcript])

  const cleanup = () => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (heartbeatRef.current) {
      window.clearInterval(heartbeatRef.current)
      heartbeatRef.current = null
    }
    mediaRecorderRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    wsRef.current = null
    void stopRecordingKeepAlive()
  }

  const startLive = async () => {
    if (!title.trim()) {
      setError('请填写课堂标题')
      return
    }
    setError('')
    setConnecting(true)
    setTranscript('')
    setSegments([])
    try {
      const recording = await api.createLiveRecording(title.trim())
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const ws = new WebSocket(api.liveStreamUrl(recording.id))
      ws.binaryType = 'arraybuffer'

      ws.onopen = () => {
        window.sessionStorage.setItem(`live-recording-${recording.id}`, '1')
        elapsedRef.current = 0
        snippetRef.current = ''
        const recorder = new MediaRecorder(stream)
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
            ws.send(e.data)
          }
        }
        recorder.start(2000)
        mediaRecorderRef.current = recorder
        streamRef.current = stream
        // 必须等待通知权限 + 前台服务启动完成，否则切后台立刻被系统回收
        startRecordingKeepAlive().then((keepAliveOk) => {
          if (!keepAliveOk) {
            // 通知权限未授予，停止已开始的录音与连接
            recorder.stop()
            stream.getTracks().forEach((t) => t.stop())
            ws.close()
            setConnecting(false)
            setError('未授予通知权限，已取消录制')
            return
          }
          setIsLive(true)
          setConnecting(false)
          setElapsedSeconds(0)
          timerRef.current = window.setInterval(() => {
            setElapsedSeconds((prev) => {
              const next = prev + 1
              elapsedRef.current = next
              // 切后台后通知栏作为"准悬浮窗"展示录制时长
              void updateRecordingNotification(next, snippetRef.current)
              return next
            })
          }, 1000)
          // 心跳保活：每 30 秒发送空字符串 ping，防止切后台时 TCP 被系统回收导致断连
          heartbeatRef.current = window.setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send('')
            }
          }, 30000)
          onStarted()
        })
      }

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data) as DownMessage
        if (msg.type === 'transcript_delta') {
          setTranscript((prev) => prev + msg.text)
          snippetRef.current = msg.text
          // 新增转写时同步刷新通知栏正文
          void updateRecordingNotification(elapsedRef.current, snippetRef.current)
        } else if (msg.type === 'segment_summary') {
          setSegments((prev) => [...prev, { seq: msg.seq, text: msg.text }])
        } else if (msg.type === 'error') {
          setError(msg.message)
        } else if (msg.type === 'done') {
          cleanup()
          setStopping(false)
          setIsLive(false)
          onFinished()
        }
      }

      ws.onerror = () => {
        setError('实时连接出错，请重试')
      }

      ws.onclose = () => {
        window.sessionStorage.removeItem(`live-recording-${recording.id}`)
        cleanup()
        setStopping(false)
        setIsLive(false)
      }

      wsRef.current = ws
    } catch (err) {
      setConnecting(false)
      setError(err instanceof Error ? err.message : '无法开始实时录制，请检查麦克风权限')
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }

  const stopLive = () => {
    // 立即反馈：后端还要收尾最后一段转写 + 生成整课总结，可能耗时较长
    setStopping(true)
    mediaRecorderRef.current?.stop()
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send('stop')
    }
  }

  return (
    <div className="card">
      <h2>实时课堂记录</h2>
      <input
        className="input"
        placeholder="课堂标题"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        disabled={isLive || connecting}
      />

      <div className="row">
        {!isLive ? (
          <button className="btn primary" onClick={startLive} disabled={connecting}>
            {connecting ? '连接中...' : '开始实时录制'}
          </button>
        ) : (
          <button className="btn danger" onClick={stopLive} disabled={stopping}>
            {stopping ? '停止中...' : '结束录制'}
          </button>
        )}
      </div>

      {isLive && (
        <div className="recording-status">
          <span className="recording-dot" />
          <span className="recording-time">{formatDuration(elapsedSeconds)}</span>
          <span className="recording-size">
            {stopping ? '正在生成课堂总结...' : '正在实时转写...'}
          </span>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      {(isLive || transcript || segments.length > 0) && (
        <div className="live-panel">
          {segments.length > 0 && (
            <div className="live-segments">
              <div className="live-panel-title">分段小结</div>
              {segments.map((seg) => (
                <div key={seg.seq} className="live-segment-item">
                  <div className="markdown-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{seg.text}</ReactMarkdown>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="live-transcript">
            <div className="live-panel-title">实时转写</div>
            <p className="text-block">
              {transcript || '正在等待语音输入...'}
              <div ref={transcriptEndRef} />
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

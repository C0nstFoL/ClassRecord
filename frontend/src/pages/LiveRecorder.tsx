import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

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
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [transcript, setTranscript] = useState('')
  const [segments, setSegments] = useState<{ seq: number; text: string }[]>([])
  const [error, setError] = useState('')

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<number | null>(null)
  const transcriptEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
      mediaRecorderRef.current?.stop()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      wsRef.current?.close()
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
    mediaRecorderRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    wsRef.current = null
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
        const recorder = new MediaRecorder(stream)
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
            ws.send(e.data)
          }
        }
        recorder.start(2000)
        mediaRecorderRef.current = recorder
        streamRef.current = stream
        setIsLive(true)
        setConnecting(false)
        setElapsedSeconds(0)
        timerRef.current = window.setInterval(() => {
          setElapsedSeconds((prev) => prev + 1)
        }, 1000)
        onStarted()
      }

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data) as DownMessage
        if (msg.type === 'transcript_delta') {
          setTranscript((prev) => prev + msg.text)
        } else if (msg.type === 'segment_summary') {
          setSegments((prev) => [...prev, { seq: msg.seq, text: msg.text }])
        } else if (msg.type === 'error') {
          setError(msg.message)
        } else if (msg.type === 'done') {
          cleanup()
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
          <button className="btn danger" onClick={stopLive}>
            结束录制
          </button>
        )}
      </div>

      {isLive && (
        <div className="recording-status">
          <span className="recording-dot" />
          <span className="recording-time">{formatDuration(elapsedSeconds)}</span>
          <span className="recording-size">正在实时转写...</span>
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
                  <div className="markdown-body">{seg.text}</div>
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

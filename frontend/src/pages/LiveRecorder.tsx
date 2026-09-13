import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, LANGUAGES, type Recording } from '../api'
import { usePresets } from '../hooks/usePresets'
import type { Preset } from '../api'
import { startRecordingKeepAlive, stopRecordingKeepAlive, updateRecordingNotification } from '../nativeRecording'
import { isNativeSttSupported, nativeStt } from '../nativeStt'

interface Props {
  onStarted: () => void
  onFinished: () => void
}

type DownMessage =
  | { type: 'transcript_full'; text: string }
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
 * 支持暂停/恢复；暂停状态下刷新页面后可从横幅继续或结束。
 */
export default function LiveRecorder({ onStarted, onFinished }: Props) {
  // 手机原生离线语音识别模式：客户端用内置中英模型识别后仅推送文字，服务器不做转写
  const sttMode = isNativeSttSupported()
  const [title, setTitle] = useState('')
  const presets: Preset[] = usePresets()
  const [preset, setPreset] = useState('default')
  const [language, setLanguage] = useState('zh')
  const [autoSummary, setAutoSummary] = useState(true)
  // 内置离线模型仅支持中英双语：其他语言回落到音频推流 + 服务器端识别
  const nativeSttActive = sttMode && (language === 'zh' || language === 'en')
  const nativeSttActiveRef = useRef(false)
  const [isLive, setIsLive] = useState(false)
  const [paused, setPaused] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [transcript, setTranscript] = useState('')
  const [partialText, setPartialText] = useState('')
  const [segments, setSegments] = useState<{ seq: number; text: string }[]>([])
  const [error, setError] = useState('')
  // 刷新恢复：检测到暂停中的录制时显示横幅
  const [resumable, setResumable] = useState<Recording | null>(null)
  const [resuming, setResuming] = useState(false)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<number | null>(null)
  const heartbeatRef = useRef<number | null>(null)
  const elapsedRef = useRef(0)
  const activeIdRef = useRef<number | null>(null)
  const snippetRef = useRef('')
  const transcriptEndRef = useRef<HTMLDivElement | null>(null)

  const clearSessionKeys = (id: number) => {
    window.sessionStorage.removeItem(`live-recording-${id}`)
    window.sessionStorage.removeItem(`live-paused-${id}`)
    window.sessionStorage.removeItem(`live-elapsed-${id}`)
    window.sessionStorage.removeItem(`live-preset-${id}`)
  }

  // 挂载时检查是否有暂停中的录制（sessionStorage 跨刷新保留）
  useEffect(() => {
    const ids = Object.keys(window.sessionStorage)
      .map((k) => /^live-recording-(\d+)$/.exec(k))
      .filter((m): m is RegExpExecArray => m !== null)
      .map((m) => Number(m[1]))
    for (const id of ids) {
      api
        .getRecording(id)
        .then((rec) => {
          if (rec.status === 'recording' && rec.is_paused) {
            setResumable(rec)
          } else if (rec.status !== 'recording') {
            clearSessionKeys(id)
          }
        })
        .catch(() => {
          /* 记录可能已被删除 */
        })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
  }, [transcript, partialText])

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
    if (nativeSttActiveRef.current) void nativeStt.stop()
    nativeSttActiveRef.current = false
    void stopRecordingKeepAlive()
  }

  const startElapsedTimer = (id: number) => {
    timerRef.current = window.setInterval(() => {
      setElapsedSeconds((prev) => {
        const next = prev + 1
        elapsedRef.current = next
        window.sessionStorage.setItem(`live-elapsed-${id}`, String(next))
        // 切后台后通知栏作为"准悬浮窗"展示录制时长
        void updateRecordingNotification(next, snippetRef.current)
        return next
      })
    }, 1000)
  }

  const startLive = async (existing?: Recording) => {
    const resumeMode = Boolean(existing)
    const resumingPaused = existing
      ? window.sessionStorage.getItem(`live-paused-${existing.id}`) === '1'
      : false
    if (!resumeMode && !title.trim()) {
      setError('请填写课堂标题')
      return
    }
    setError('')
    setConnecting(true)
    setResuming(resumeMode)
    if (!resumeMode) {
      setTranscript('')
      setSegments([])
    }
    try {
      const recording = existing ?? (await api.createLiveRecording(title.trim(), language, autoSummary))
      const recLanguage = existing ? existing.language : language
      const recPreset = existing
        ? (window.sessionStorage.getItem(`live-preset-${recording.id}`) ?? 'default')
        : preset
      // 内置离线模型仅支持中英双语
      const nativeMode = sttMode && (recLanguage === 'zh' || recLanguage === 'en')
      nativeSttActiveRef.current = nativeMode
      activeIdRef.current = recording.id

      // 原生离线识别模式：识别在手机本地完成，先确保麦克风运行时权限已授予
      if (nativeMode) {
        const check = await nativeStt.checkPermission()
        if (!check.granted) {
          const req = await nativeStt.requestPermission()
          if (!req.granted) {
            setConnecting(false)
            setResuming(false)
            setError('未授予麦克风权限，无法使用语音识别')
            return
          }
        }
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const ws = new WebSocket(api.liveStreamUrl(recording.id, recPreset))

      ws.onopen = () => {
        window.sessionStorage.setItem(`live-recording-${recording.id}`, '1')
        window.sessionStorage.setItem(`live-preset-${recording.id}`, recPreset)
        // 恢复时还原累计时长；全新录制从 0 开始
        if (resumeMode) {
          elapsedRef.current = Number(window.sessionStorage.getItem(`live-elapsed-${recording.id}`) ?? 0)
        } else {
          elapsedRef.current = 0
          window.sessionStorage.setItem(`live-elapsed-${recording.id}`, '0')
        }
        snippetRef.current = ''
        setIsLive(true)
        setPaused(false)
        setConnecting(false)
        setResuming(false)
        setElapsedSeconds(elapsedRef.current)
        startElapsedTimer(recording.id)
        // 心跳保活：每 30 秒发送空字符串 ping，防止切后台时 TCP 被系统回收导致断连
        heartbeatRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send('')
          }
        }, 30000)
        onStarted()

        // 恢复暂停中的录制：通知服务器从新分片继续
        if (resumeMode && resumingPaused) {
          ws.send(JSON.stringify({ type: 'resume' }))
          window.sessionStorage.removeItem(`live-paused-${recording.id}`)
        }

        if (nativeMode) {
          // 原生识别模式：释放 WebView 占用的麦克风，由本地离线模型接管
          stream.getTracks().forEach((t) => t.stop())
          streamRef.current = null
          nativeStt.onPartial((text) => setPartialText(text))
          nativeStt.onFinal((text) => {
            setPartialText('')
            if (!text) return
            setTranscript((prev) => prev + text + ' ')
            snippetRef.current = text
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: 'stt_text', text }))
            }
            void updateRecordingNotification(elapsedRef.current, text)
          })
          nativeStt.onError((msg) => setError(msg))
          nativeStt.start(recLanguage === 'en' ? 'en-US' : 'zh-CN').catch((e) => {
            ws.close()
            setError(e instanceof Error ? e.message : '启动语音识别失败')
          })
          return
        }

        // 音频推流模式
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
            setResuming(false)
            setError('未授予通知权限，已取消录制')
          }
        })
      }

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data) as DownMessage
        if (msg.type === 'transcript_full') {
          // 服务器周期性推送全量文本（可能修订更早的识别结果），整体替换渲染
          setTranscript(msg.text)
          snippetRef.current = msg.text.slice(-80)
          // 转写更新时同步刷新通知栏正文
          void updateRecordingNotification(elapsedRef.current, snippetRef.current)
        } else if (msg.type === 'segment_summary') {
          setSegments((prev) => [...prev, { seq: msg.seq, text: msg.text }])
        } else if (msg.type === 'error') {
          setError(msg.message)
        } else if (msg.type === 'done') {
          if (activeIdRef.current !== null) clearSessionKeys(activeIdRef.current)
          activeIdRef.current = null
          cleanup()
          setStopping(false)
          setIsLive(false)
          setPaused(false)
          onFinished()
        }
      }

      ws.onerror = () => {
        setError('实时连接出错，请重试')
      }

      ws.onclose = () => {
        // 暂停中断开（如刷新）：保留 sessionStorage 标记以便恢复横幅出现
        if (!paused && activeIdRef.current !== null) {
          clearSessionKeys(activeIdRef.current)
        }
        activeIdRef.current = null
        cleanup()
        setStopping(false)
        setIsLive(false)
        setPaused(false)
      }

      wsRef.current = ws
    } catch (err) {
      setConnecting(false)
      setResuming(false)
      setError(err instanceof Error ? err.message : '无法开始实时录制，请检查麦克风权限')
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }

  const stopLive = () => {
    // 立即反馈：后端还要收尾最后一段转写 + 生成整课总结，可能耗时较长
    setStopping(true)
    if (nativeSttActiveRef.current) {
      // 先停止本地识别，让最后一次 final 结果有机会推送到服务器，再通知收尾
      void nativeStt.stop()
      window.setTimeout(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send('stop')
        }
      }, 800)
      return
    }
    mediaRecorderRef.current?.stop()
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send('stop')
    }
  }

  const pauseLive = async () => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN || paused) return
    if (nativeSttActiveRef.current) {
      void nativeStt.stop()
      setPartialText('')
    } else {
      // 彻底停止 MediaRecorder（而非 pause()）：恢复时需新建 MediaRecorder——
      // resume() 续写的裸 cluster 缺 webm 文件头，新分片无法解码。
      // 先等 stop 事件把最后一片音频冲刷出去，再通知服务器暂停，保证
      // 当前 webm 分片完整且不丢暂停边界的音频。
      const rec = mediaRecorderRef.current
      mediaRecorderRef.current = null
      if (rec && rec.state !== 'inactive') {
        await new Promise<void>((resolve) => {
          rec.addEventListener('stop', () => resolve(), { once: true })
          rec.stop()
        })
      }
    }
    ws.send(JSON.stringify({ type: 'pause' }))
    setPaused(true)
    if (activeIdRef.current !== null) {
      window.sessionStorage.setItem(`live-paused-${activeIdRef.current}`, '1')
    }
  }

  const resumeLive = () => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN || !paused) return
    // 先发控制帧让服务器开好新分片，再恢复产音
    ws.send(JSON.stringify({ type: 'resume' }))
    setPaused(false)
    if (activeIdRef.current !== null) {
      window.sessionStorage.removeItem(`live-paused-${activeIdRef.current}`)
    }
    if (nativeSttActiveRef.current) {
      nativeStt.start(language === 'en' ? 'en-US' : 'zh-CN').catch((e) => {
        setError(e instanceof Error ? e.message : '恢复语音识别失败')
      })
    } else {
      // 新建 MediaRecorder：新分片自带 webm 文件头，可独立解码
      const stream = streamRef.current
      if (stream) {
        const recorder = new MediaRecorder(stream)
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
            ws.send(e.data)
          }
        }
        recorder.start(2000)
        mediaRecorderRef.current = recorder
      }
    }
  }

  const finishResumable = async () => {
    if (!resumable) return
    setStopping(true)
    try {
      await api.finishRecording(resumable.id)
      clearSessionKeys(resumable.id)
      setResumable(null)
      onFinished()
    } catch (err) {
      setError(err instanceof Error ? err.message : '结束录制失败')
    } finally {
      setStopping(false)
    }
  }

  return (
    <div className="card">
      <h2>实时课堂记录</h2>

      {resumable && !isLive && (
        <div className="resume-banner">
          <span className="hint">
            「{resumable.title}」录制已暂停
          </span>
          <div className="resume-actions">
            <button className="btn small primary" onClick={() => void startLive(resumable)} disabled={resuming || connecting}>
              {resuming ? '连接中...' : '继续录制'}
            </button>
            <button className="btn small danger" onClick={finishResumable} disabled={stopping}>
              {stopping ? '正在结束...' : '结束录制'}
            </button>
          </div>
        </div>
      )}

      <input
        className="input"
        placeholder="课堂标题"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        disabled={isLive || connecting}
      />

      <select
        className="input"
        value={language}
        onChange={(e) => setLanguage(e.target.value)}
        disabled={isLive || connecting}
        aria-label="识别语言"
      >
        {LANGUAGES.map((l) => (
          <option key={l.key} value={l.key}>
            {l.label}
          </option>
        ))}
      </select>

      <select
        className="input"
        value={preset}
        onChange={(e) => setPreset(e.target.value)}
        disabled={isLive || connecting}
        aria-label="课程内容类型"
        hidden={nativeSttActive}
      >
        {presets.length === 0 && <option value="default">默认增强</option>}
        {presets.map((p) => (
          <option key={p.key} value={p.key}>
            {p.label}
          </option>
        ))}
      </select>

      <label className="check-row">
        <input
          type="checkbox"
          checked={autoSummary}
          onChange={(e) => setAutoSummary(e.target.checked)}
          disabled={isLive || connecting}
        />
        录制结束后自动生成课堂总结
      </label>

      <div className="row">
        {!isLive ? (
          <button className="btn primary" onClick={() => void startLive()} disabled={connecting || resuming}>
            {connecting ? '连接中...' : '开始实时录制'}
          </button>
        ) : paused ? (
          <>
            <button className="btn primary" onClick={resumeLive}>
              继续录制
            </button>
            <button className="btn danger" onClick={stopLive} disabled={stopping}>
              {stopping ? '停止中...' : '结束录制'}
            </button>
          </>
        ) : (
          <>
            <button className="btn" onClick={() => void pauseLive()} disabled={stopping}>
              ⏸ 暂停
            </button>
            <button className="btn danger" onClick={stopLive} disabled={stopping}>
              {stopping ? '停止中...' : '结束录制'}
            </button>
          </>
        )}
      </div>

      {isLive && (
        <div className="recording-status">
          <span className={paused ? 'recording-dot paused' : 'recording-dot'} />
          <span className="recording-time">{formatDuration(elapsedSeconds)}</span>
          <span className="recording-size">
            {stopping ? '正在生成课堂总结...' : paused ? '已暂停' : '正在实时转写...'}
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
              {partialText && <span className="live-partial">{partialText}</span>}
              <div ref={transcriptEndRef} />
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

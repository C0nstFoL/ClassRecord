import { useEffect, useRef, useState } from 'react'
import { api, LANGUAGES } from '../api'
import { usePresets } from '../hooks/usePresets'
import type { Preset } from '../api'

interface Props {
  onUploaded: () => void
}

function formatDuration(totalSeconds: number) {
  const m = Math.floor(totalSeconds / 60)
  const s = totalSeconds % 60
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

/**
 * 支持两种录音来源：
 * 1) 浏览器 MediaRecorder 实时录音
 * 2) 直接选择已有的音视频文件上传
 */
export default function Recorder({ onUploaded }: Props) {
  const [title, setTitle] = useState('')
  const presets: Preset[] = usePresets()
  const [preset, setPreset] = useState('default')
  const [language, setLanguage] = useState('zh')
  const [isRecording, setIsRecording] = useState(false)
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  // 录制进度：耗时（秒）、已捕获的数据大小（字节）
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [capturedSize, setCapturedSize] = useState(0)
  const [finalDuration, setFinalDuration] = useState(0)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<number | null>(null)
  const elapsedRef = useRef(0)

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [])

  const startRecording = async () => {
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      chunksRef.current = []
      setElapsedSeconds(0)
      setCapturedSize(0)
      elapsedRef.current = 0
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data)
          setCapturedSize((prev) => prev + e.data.size)
        }
      }
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        setRecordedBlob(blob)
        setFinalDuration(elapsedRef.current)
        stream.getTracks().forEach((track) => track.stop())
      }
      // 每秒切片一次，便于实时展示已录制的大小
      recorder.start(1000)
      mediaRecorderRef.current = recorder
      setIsRecording(true)
      setSelectedFile(null)

      timerRef.current = window.setInterval(() => {
        setElapsedSeconds((prev) => {
          elapsedRef.current = prev + 1
          return elapsedRef.current
        })
      }, 1000)
    } catch (err) {
      setError('无法访问麦克风，请检查浏览器权限设置')
      console.error(err)
    }
  }

  const stopRecording = () => {
    mediaRecorderRef.current?.stop()
    setIsRecording(false)
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null
    setSelectedFile(file)
    setRecordedBlob(null)
  }

  const handleUpload = async () => {
    const blob = recordedBlob ?? selectedFile
    if (!blob) {
      setError('请先录音或选择文件')
      return
    }
    if (!title.trim()) {
      setError('请填写课堂标题')
      return
    }
    setError('')
    setUploading(true)
    try {
      const filename = selectedFile ? selectedFile.name : `recording-${Date.now()}.webm`
      await api.uploadRecording(blob, title.trim(), filename, preset, language)
      setTitle('')
      setRecordedBlob(null)
      setSelectedFile(null)
      setElapsedSeconds(0)
      setCapturedSize(0)
      setFinalDuration(0)
      onUploaded()
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const readyBlob = recordedBlob ?? selectedFile

  return (
    <div className="card">
      <h2>新建课堂记录</h2>
      <input
        className="input"
        placeholder="课堂标题"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />

      <select
        className="input"
        value={language}
        onChange={(e) => setLanguage(e.target.value)}
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
        aria-label="课程内容类型"
      >
        {presets.length === 0 && <option value="default">默认增强</option>}
        {presets.map((p) => (
          <option key={p.key} value={p.key}>
            {p.label}
          </option>
        ))}
      </select>

      <div className="row">
        {!isRecording ? (
          <button className="btn" onClick={startRecording}>
            开始录音
          </button>
        ) : (
          <button className="btn danger" onClick={stopRecording}>
            停止录音
          </button>
        )}
        <span className="or-text">或</span>
        <input type="file" accept="audio/*,video/*" onChange={handleFileChange} />
      </div>

      {isRecording && (
        <div className="recording-status">
          <span className="recording-dot" />
          <span className="recording-time">{formatDuration(elapsedSeconds)}</span>
          <span className="recording-size">{formatSize(capturedSize)}</span>
        </div>
      )}

      {readyBlob && !isRecording && (
        <div className="ready-info">
          <span className="ready-info-name">
            {recordedBlob ? `录音-${formatDuration(finalDuration)}.webm` : selectedFile?.name}
          </span>
          <span className="ready-info-meta">
            {recordedBlob ? `时长 ${formatDuration(finalDuration)} · ` : ''}
            {formatSize(readyBlob.size)}
          </span>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      <button className="btn primary" disabled={uploading || isRecording} onClick={handleUpload}>
        {uploading ? '上传中...' : '上传并开始转写总结'}
      </button>
    </div>
  )
}

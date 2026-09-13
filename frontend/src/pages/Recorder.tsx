import { useState } from 'react'
import { api, LANGUAGES } from '../api'
import { usePresets } from '../hooks/usePresets'
import type { Preset } from '../api'

interface Props {
  onUploaded: () => void
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

/** 上传已有的音视频文件，创建新的课堂记录。 */
export default function Recorder({ onUploaded }: Props) {
  const [title, setTitle] = useState('')
  const presets: Preset[] = usePresets()
  const [preset, setPreset] = useState('default')
  const [language, setLanguage] = useState('zh')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [autoSummary, setAutoSummary] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSelectedFile(e.target.files?.[0] ?? null)
  }

  const handleUpload = async () => {
    if (!selectedFile) {
      setError('请先选择文件')
      return
    }
    if (!title.trim()) {
      setError('请填写课堂标题')
      return
    }
    setError('')
    setUploading(true)
    try {
      await api.uploadRecording(selectedFile, title.trim(), selectedFile.name, preset, language, autoSummary)
      setTitle('')
      setSelectedFile(null)
      onUploaded()
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

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
        <input type="file" accept="audio/*,video/*" onChange={handleFileChange} />
      </div>

      <label className="check-row">
        <input
          type="checkbox"
          checked={autoSummary}
          onChange={(e) => setAutoSummary(e.target.checked)}
        />
        转写完成后自动生成课堂总结
      </label>

      {selectedFile && (
        <div className="ready-info">
          <span className="ready-info-name">{selectedFile.name}</span>
          <span className="ready-info-meta">{formatSize(selectedFile.size)}</span>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      <button className="btn primary" disabled={uploading} onClick={handleUpload}>
        {uploading ? '上传中...' : '上传并开始转写总结'}
      </button>
    </div>
  )
}

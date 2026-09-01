import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import { api, type Recording } from './api'
import LoginGate from './pages/LoginGate'
import Recorder from './pages/Recorder'
import RecordingDetail from './pages/RecordingDetail'
import RecordingList from './pages/RecordingList'
import ThemeSwitch from './pages/ThemeSwitch'
import { useTheme } from './useTheme'

const ACTIVE_STATUSES: Recording['status'][] = ['uploaded', 'transcribing', 'summarizing']

function Dashboard({ userName }: { userName: string | null }) {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const pollTimer = useRef<number | null>(null)
  const { mode, setMode } = useTheme()

  const refresh = useCallback(async () => {
    const list = await api.listRecordings()
    setRecordings(list)
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // 只要有记录处于处理中状态，就定时轮询刷新，避免用户手动刷新页面
  useEffect(() => {
    const hasActive = recordings.some((r) => ACTIVE_STATUSES.includes(r.status))
    if (hasActive) {
      pollTimer.current = window.setTimeout(refresh, 3000)
    }
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current)
    }
  }, [recordings, refresh])

  const handleDelete = async (id: number) => {
    await api.deleteRecording(id)
    if (selectedId === id) setSelectedId(null)
    refresh()
  }

  const handleSelect = (id: number) => {
    setSelectedId((prev) => (prev === id ? null : id))
  }

  const selected = recordings.find((r) => r.id === selectedId) ?? null

  return (
    <div className="app">
      <header className="header">
        <h1>课堂记录助手</h1>
        <div>
          <ThemeSwitch mode={mode} onChange={setMode} />
          <span className="hint">{userName}</span>
          <button
            className="btn small"
            onClick={async () => {
              await api.logout()
              window.location.reload()
            }}
          >
            退出登录
          </button>
        </div>
      </header>

      <main className="main">
        <div className="left-col">
          <Recorder onUploaded={refresh} />
          <RecordingList
            recordings={recordings}
            selectedId={selectedId}
            onSelect={handleSelect}
            onDelete={handleDelete}
          />
        </div>
        <div className="right-col">
          <RecordingDetail recording={selected} onRetried={refresh} />
        </div>
      </main>
    </div>
  )
}

export default function App() {
  return <LoginGate>{(user) => <Dashboard userName={user.name ?? user.email} />}</LoginGate>
}

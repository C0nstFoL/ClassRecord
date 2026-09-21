import { useCallback, useEffect, useState } from 'react'
import './App.css'
import { api, type Recording, type RecordingListItem } from './api'
import LiveRecorder from './pages/LiveRecorder'
import LoginGate from './pages/LoginGate'
import Recorder from './pages/Recorder'
import RecordingDetail from './pages/RecordingDetail'
import RecordingList from './pages/RecordingList'
import SharePage from './pages/SharePage'
import ThemeSwitch from './pages/ThemeSwitch'
import UpdateChecker from './pages/UpdateChecker'
import { usePullToRefresh } from './usePullToRefresh'
import { useTheme } from './useTheme'

const ACTIVE_STATUSES: Recording['status'][] = [
  'uploaded',
  'transcribing',
  'summarizing',
  'recording',
]

type RecordMode = 'upload' | 'live'

function Dashboard({ userName }: { userName: string | null }) {
  const [recordings, setRecordings] = useState<RecordingListItem[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selectedRecording, setSelectedRecording] = useState<Recording | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailRevision, setDetailRevision] = useState(0)
  const [recordMode, setRecordMode] = useState<RecordMode>('upload')
  // 移动端（<860px）双视图：false 显示列表，true 显示详情（带返回按钮）
  const [mobileShowDetail, setMobileShowDetail] = useState(false)
  const { mode, setMode } = useTheme()
  const pull = usePullToRefresh()

  const refresh = useCallback(async () => {
    try {
      const list = await api.listRecordings()
      setRecordings(list)
    } catch {
      // 单次刷新失败（网络抖动等）不抛出：保住轮询链，下次到点继续尝试
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const selectedStatus = recordings.find((recording) => recording.id === selectedId)?.status

  // 列表只取轻量元数据；完整转写与总结仅在用户打开详情时按需加载。
  // 处理中记录跨状态变化时重载一次详情，不随 updated_at 每 3 秒重复下载全文。
  useEffect(() => {
    if (selectedId === null) return
    let cancelled = false
    api
      .getRecording(selectedId)
      .then((recording) => {
        if (!cancelled) setSelectedRecording(recording)
      })
      .catch((err) => {
        if (!cancelled) {
          setSelectedRecording(null)
          setDetailError(err instanceof Error ? err.message : '加载记录详情失败')
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId, selectedStatus, detailRevision])

  const refreshAll = useCallback(() => {
    void refresh()
    if (selectedId !== null) {
      setDetailLoading(true)
      setDetailError(null)
    }
    setDetailRevision((revision) => revision + 1)
  }, [refresh, selectedId])

  // 只要有记录处于处理中状态，就定时轮询刷新，避免用户手动刷新页面。
  // 用 interval 而非 setTimeout 链：单次刷新失败（recordings 不变）时轮询依然继续
  useEffect(() => {
    const hasActive = recordings.some((r) => ACTIVE_STATUSES.includes(r.status))
    if (!hasActive) return
    const timer = window.setInterval(refresh, 3000)
    return () => {
      window.clearInterval(timer)
    }
  }, [recordings, refresh])

  const handleDelete = async (id: number) => {
    await api.deleteRecording(id)
    if (selectedId === id) {
      setSelectedId(null)
      setSelectedRecording(null)
      setMobileShowDetail(false)
    }
    refresh()
  }

  // 合并多段中断拆分的记录：保留最早的一条作为主记录
  const handleMerge = async (ids: number[]) => {
    const items = ids
      .map((id) => recordings.find((r) => r.id === id))
      .filter((r): r is RecordingListItem => Boolean(r))
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    if (items.length < 2) return
    const [main, ...sources] = items
    const ok = window.confirm(
      `将把 ${items.length} 条记录按时间顺序合并为「${main.title}」，\n` +
        `其余 ${sources.length} 条记录将被删除，整体总结会重新生成。\n确定合并？`,
    )
    if (!ok) return
    await api.mergeRecordings(main.id, sources.map((r) => r.id))
    if (selectedId !== null && sources.some((r) => r.id === selectedId)) {
      setSelectedId(main.id)
    }
    refresh()
  }

  const handleSelect = (id: number) => {
    const next = selectedId === id ? null : id
    setSelectedId(next)
    setSelectedRecording(null)
    setDetailError(null)
    setDetailLoading(next !== null)
    setMobileShowDetail(next !== null)
    if (next !== null) window.scrollTo({ top: 0 })
  }

  const handleHomeworkCreated = (id: number) => {
    setSelectedId(id)
    setSelectedRecording(null)
    setDetailLoading(true)
    setDetailError(null)
    setMobileShowDetail(true)
    void refresh()
  }

  return (
    <div className="app">
      {pull > 0 && (
        <div
          className="pull-refresh-indicator"
          style={{ transform: `translateY(${pull}px)` }}
        >
          <span className={`pull-refresh-spinner ${pull >= 80 ? 'ready' : ''}`} />
        </div>
      )}
      <header className="header">
        <h1>课堂记录助手</h1>
        <div>
          <ThemeSwitch mode={mode} onChange={setMode} />
          <span className="hint">{userName}</span>
          <button
            className="btn small"
            title="检查应用更新"
            onClick={() => window.dispatchEvent(new Event('check-app-update'))}
          >
            ⤓
          </button>
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

      <main className={`main ${mobileShowDetail ? 'mobile-show-detail' : ''}`}>
        <div className="left-col">
          <div className="record-mode-tabs">
            <button
              className={`record-mode-tab ${recordMode === 'upload' ? 'active' : ''}`}
              onClick={() => setRecordMode('upload')}
            >
              上传录音
            </button>
            <button
              className={`record-mode-tab ${recordMode === 'live' ? 'active' : ''}`}
              onClick={() => setRecordMode('live')}
            >
              实时录制
            </button>
          </div>
          {recordMode === 'upload' ? (
            <Recorder onUploaded={refresh} />
          ) : (
            <LiveRecorder onStarted={refresh} onFinished={refreshAll} />
          )}
          <RecordingList
            recordings={recordings}
            selectedId={selectedId}
            onSelect={handleSelect}
            onDelete={handleDelete}
            onMerge={handleMerge}
            onHomeworkCreated={handleHomeworkCreated}
          />
        </div>
        <div className="right-col">
          <button className="btn small mobile-back" onClick={() => setMobileShowDetail(false)}>
            ← 返回列表
          </button>
          {detailLoading ? (
            <div className="card">正在加载记录详情...</div>
          ) : detailError ? (
            <div className="card">
              <p className="error">{detailError}</p>
              <button className="btn small" onClick={refreshAll}>重新加载</button>
            </div>
          ) : (
            <RecordingDetail
              recording={selectedRecording}
              onRetried={refreshAll}
              onChanged={refreshAll}
              onSelectRecording={handleSelect}
            />
          )}
        </div>
      </main>
    </div>
  )
}

export default function App() {
  // 分享链接路由（/s/{token}）：无需登录，SPA fallback 会把该路径交给前端处理
  const shareMatch = window.location.pathname.match(/^\/s\/([A-Za-z0-9_-]+)\/?$/)
  if (shareMatch) {
    return <SharePage token={shareMatch[1]} />
  }
  return (
    <>
      <UpdateChecker />
      <LoginGate>{(user) => <Dashboard userName={user.name ?? user.email} />}</LoginGate>
    </>
  )
}

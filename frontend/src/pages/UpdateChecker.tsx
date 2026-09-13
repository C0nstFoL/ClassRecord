import { useCallback, useEffect, useRef, useState } from 'react'
import { Capacitor } from '@capacitor/core'
import { api } from '../api'
import AutoUpdate, { type DownloadProgress } from '../nativeAutoUpdate'

interface UpdateInfo {
  version_name: string
  version_code: number
  apk_url: string | null
  changelog: string
  force: boolean
}

/**
 * 应用内更新检查器（仅安卓原生环境生效）：
 * 启动时自动检查一次，有新版本时弹出更新提示；
 * 也可通过 window 派发 "check-app-update" 事件手动触发检查（如头部按钮）。
 */
export default function UpdateChecker() {
  const [update, setUpdate] = useState<UpdateInfo | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [needPermission, setNeedPermission] = useState(false)
  const [dismissed, setDismissed] = useState<number | null>(null)
  const progressListenerRef = useRef<{ remove: () => Promise<void> } | null>(null)

  const isAndroid = Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android'

  const check = useCallback(async () => {
    if (!isAndroid) return
    try {
      const info = await AutoUpdate.getAppInfo()
      const res = await api.checkAppUpdate(info.versionCode)
      if (res.has_update && res.apk_url && res.version_code !== dismissed) {
        setUpdate(res as UpdateInfo)
      }
    } catch {
      /* 检查更新失败静默忽略 */
    }
  }, [isAndroid, dismissed])

  useEffect(() => {
    void check()
    const handler = () => void check()
    window.addEventListener('check-app-update', handler)
    return () => {
      window.removeEventListener('check-app-update', handler)
      void progressListenerRef.current?.remove()
    }
  }, [check])

  const startUpdate = async () => {
    if (!update?.apk_url || downloading) return
    setError('')
    try {
      const perm = await AutoUpdate.canInstall()
      if (!perm.granted) {
        // 跳转系统设置授予「安装未知应用」权限，返回后用户再次点击即可
        await AutoUpdate.openInstallSettings()
        setNeedPermission(true)
        return
      }
      setNeedPermission(false)
      setDownloading(true)
      setProgress(0)
      progressListenerRef.current = await AutoUpdate.addListener('downloadProgress', (data: DownloadProgress) => {
        setProgress(data.progress)
      })
      await AutoUpdate.downloadAndInstall({
        url: `${window.location.origin}${update.apk_url}`,
      })
      // 安装器接管后 App 会进入后台，无需额外处理
    } catch (err) {
      setError(err instanceof Error ? err.message : '下载更新失败')
    } finally {
      setDownloading(false)
      void progressListenerRef.current?.remove()
      progressListenerRef.current = null
    }
  }

  const close = () => {
    if (!update || update.force || downloading) return
    setDismissed(update.version_code)
    setUpdate(null)
    setError('')
    setNeedPermission(false)
  }

  if (!update) return null

  const visible = update.force || dismissed !== update.version_code
  if (!visible) return null

  return (
    <div className="update-overlay" onClick={update.force ? undefined : close}>
      <div className="update-dialog" onClick={(e) => e.stopPropagation()}>
        <h3>发现新版本 v{update.version_name}</h3>
        {update.changelog && <p className="update-changelog">{update.changelog}</p>}
        {needPermission && (
          <p className="hint">已跳转到系统设置，请允许「安装未知应用」后返回，再点击立即更新</p>
        )}
        {downloading && (
          <div className="update-progress">
            <div className="update-progress-bar">
              <div className="update-progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <span className="hint">{progress}%</span>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <div className="update-actions">
          {!update.force && !downloading && (
            <button className="btn" onClick={close}>
              以后再说
            </button>
          )}
          <button className="btn primary" onClick={startUpdate} disabled={downloading}>
            {downloading ? '下载中...' : '立即更新'}
          </button>
        </div>
      </div>
    </div>
  )
}

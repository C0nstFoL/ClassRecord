import { Capacitor, registerPlugin } from '@capacitor/core'
import {
  ForegroundService,
  ServiceType,
} from '@capawesome-team/capacitor-android-foreground-service'
import { KeepAwake } from '@capacitor-community/keep-awake'

const isAndroidNative =
  Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android'

/**
 * 自定义原生插件：直接走 AndroidX ActivityResultContracts.RequestPermission 弹系统通知权限框。
 * 绕开 Capacitor 抽象，避免华为等 ROM 上系统弹窗被吞的问题。
 */
interface NotificationPermissionPlugin {
  check(): Promise<{ granted: boolean; sdkInt: number }>
  request(): Promise<{ granted: boolean; triggered: boolean; sdkInt?: number }>
  addListener(eventName: 'permissionResult', listener: (data: { granted: boolean }) => void): Promise<void>
  removeListener(eventName: 'permissionResult', listener: (data: { granted: boolean }) => void): Promise<void>
}

const NotificationPermission = registerPlugin<NotificationPermissionPlugin>(
  'NotificationPermission'
)

/**
 * 检查 + 主动请求通知权限 (POST_NOTIFICATIONS, Android 13+)。
 * 返回最终是否被授予，附带详细日志便于排查。
 */
export async function ensureNotificationPermission(): Promise<boolean> {
  if (!isAndroidNative) return true
  try {
    const state = await NotificationPermission.check()
    console.log('[通知权限] 当前状态:', state)
    if (state.granted) return true

    console.log('[通知权限] 未授予，发起系统弹窗')
    const req = await NotificationPermission.request()
    console.log('[通知权限] request 返回:', req)
    if (req.granted) return true

    // 弹窗后用户拒绝；等待回调
    if (req.triggered) {
      const granted = await new Promise<boolean>((resolve) => {
        const handler = (data: { granted: boolean }) => {
          console.log('[通知权限] 系统弹窗回调:', data)
          resolve(data.granted)
          void NotificationPermission.removeListener('permissionResult', handler)
        }
        void NotificationPermission.addListener('permissionResult', handler)
        // 60 秒兜底超时
        setTimeout(() => {
          console.warn('[通知权限] 等待系统弹窗回调超时')
          resolve(false)
          void NotificationPermission.removeListener('permissionResult', handler)
        }, 60000)
      })
      return granted
    }

    // 弹窗无法触发（极少见，例如 Activity 已销毁），引导用户去设置
    window.alert(
      '录制需要通知权限才能在后台保持运行。\n\n' +
        '请前往：系统设置 → 应用 → 课堂记录助手 → 通知，开启通知权限后重新进入录制。'
    )
    return false
  } catch (err) {
    console.warn('[通知权限] 检查失败', err)
    return false
  }
}

/**
 * 实时录制期间的原生保活（仅 Android App 生效，Web 端为空操作）：
 * 1. microphone 类型前台服务 —— 锁屏/切后台后系统不再挂起麦克风采集
 * 2. KeepAwake —— 保持 CPU 不休眠，兜底
 *
 * 若通知权限未授予，返回 false，由调用方阻断录制启动。
 */
export async function startRecordingKeepAlive(): Promise<boolean> {
  if (!isAndroidNative) return true
  const granted = await ensureNotificationPermission()
  if (!granted) {
    console.warn('[前台服务] 通知权限未授予，不启动')
    return false
  }
  try {
    await ForegroundService.startForegroundService({
      title: '课堂记录助手',
      body: '正在实时录制与转写课堂音频，请勿关闭应用',
      id: 1,
      smallIcon: 'ic_stat_recording',
      serviceType: ServiceType.Microphone,
    })
    console.log('[前台服务] 已启动')
  } catch (err) {
    console.warn('[前台服务] 启动失败，后台录制可能中断', err)
    return false
  }
  try {
    await KeepAwake.keepAwake()
    console.log('[KeepAwake] 已开启')
  } catch (err) {
    console.warn('[KeepAwake] 失败', err)
  }
  return true
}

export async function stopRecordingKeepAlive(): Promise<void> {
  if (!isAndroidNative) return
  try {
    await ForegroundService.stopForegroundService()
    console.log('[前台服务] 已停止')
  } catch (err) {
    console.warn('[前台服务] 停止失败', err)
  }
  try {
    await KeepAwake.allowSleep()
  } catch (err) {
    console.warn('[KeepAwake] 释放失败', err)
  }
}

/**
 * 刷新前台服务通知栏内容（仅 Android App 生效）：
 * - 标题：录制时长 mm:ss
 * - 正文：最近一句转写（截取末尾 60 字符）
 * - 点击通知由插件默认行为拉起 App
 */
export async function updateRecordingNotification(
  elapsedSeconds: number,
  snippet: string
): Promise<void> {
  if (!isAndroidNative) return
  const mm = Math.floor(elapsedSeconds / 60).toString().padStart(2, '0')
  const ss = (elapsedSeconds % 60).toString().padStart(2, '0')
  const trimmed = (snippet || '').replace(/\s+/g, ' ').trim().slice(-60)
  try {
    await ForegroundService.updateForegroundService({
      id: 1,
      title: `课堂记录助手 · ${mm}:${ss}`,
      body: trimmed || '正在实时转写...',
      smallIcon: 'ic_stat_recording',
      serviceType: ServiceType.Microphone,
      silent: true,
    })
  } catch (err) {
    console.warn('[通知更新] 失败', err)
  }
}

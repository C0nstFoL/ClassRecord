import { Capacitor, registerPlugin } from '@capacitor/core'
import type { PluginListenerHandle } from '@capacitor/core'

interface NativeSpeechPlugin {
  checkPermission(): Promise<{ granted: boolean; available: boolean }>
  requestPermission(): Promise<{ granted: boolean; triggered: boolean }>
  start(options?: { language?: string }): Promise<void>
  stop(): Promise<void>
  addListener(eventName: 'partial', cb: (data: { text: string }) => void): Promise<PluginListenerHandle>
  addListener(eventName: 'final', cb: (data: { text: string }) => void): Promise<PluginListenerHandle>
  addListener(eventName: 'ready', cb: (data: { text: string }) => void): Promise<PluginListenerHandle>
  addListener(eventName: 'error', cb: (data: { text: string }) => void): Promise<PluginListenerHandle>
  addListener(eventName: 'permissionResult', cb: (data: { granted: boolean }) => void): Promise<PluginListenerHandle>
}

const NativeSpeech = registerPlugin<NativeSpeechPlugin>('NativeSpeech')

/**
 * 手机系统语音识别：仅 Android 原生 App 可用（iOS 端后续用 SFSpeechRecognizer 实现）。
 * Web / 浏览器环境返回 false，走原有的 MediaRecorder 音频推流模式。
 */
export function isNativeSttSupported(): boolean {
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android'
}

export const nativeStt = {
  checkPermission: () => NativeSpeech.checkPermission(),
  requestPermission: () => NativeSpeech.requestPermission(),
  start: (language = 'zh-CN') => NativeSpeech.start({ language }),
  stop: () => NativeSpeech.stop(),
  onPartial: (cb: (text: string) => void) =>
    NativeSpeech.addListener('partial', (d) => cb(d.text)),
  onFinal: (cb: (text: string) => void) =>
    NativeSpeech.addListener('final', (d) => cb(d.text)),
  onError: (cb: (text: string) => void) =>
    NativeSpeech.addListener('error', (d) => cb(d.text)),
}

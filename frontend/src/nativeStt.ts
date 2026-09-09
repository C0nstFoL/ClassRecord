import { registerPlugin } from '@capacitor/core'
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
 * 内置离线语音识别已下线：应用轻量化后统一走音频推流 + 服务器端转写
 * （中文/粤语 SenseVoice，其他语言 Whisper）。插件代码保留在仓库历史中，
 * 需要离线能力时可从 git 历史恢复（commit 8795340 / 7f7a755 之前）。
 */
export function isNativeSttSupported(): boolean {
  return false
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

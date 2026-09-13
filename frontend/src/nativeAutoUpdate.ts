import { registerPlugin } from '@capacitor/core'

export interface AppInfoResult {
  versionName: string
  versionCode: number
}

export interface DownloadProgress {
  received: number
  total: number
  progress: number
}

export interface AutoUpdatePluginInterface {
  getAppInfo(): Promise<AppInfoResult>
  canInstall(): Promise<{ granted: boolean }>
  openInstallSettings(): Promise<void>
  downloadAndInstall(options: { url: string }): Promise<void>
  addListener(
    eventName: 'downloadProgress',
    listenerFunc: (data: DownloadProgress) => void,
  ): Promise<{ remove: () => Promise<void> }>
}

const AutoUpdate = registerPlugin<AutoUpdatePluginInterface>('AutoUpdate')

export default AutoUpdate

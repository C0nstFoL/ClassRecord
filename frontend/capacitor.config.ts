import type { CapacitorConfig } from '@capacitor/cli'

/**
 * 远程壳模式：WebView 直接加载线上站点，
 * 登录/cookie/WebSocket 全部复用现有同源链路，后端零改动。
 * webDir (dist/) 仅作为 Capacitor 必需的占位目录，实际页面不使用本地资源。
 */
const config: CapacitorConfig = {
  appId: 'site.folink.classrecord',
  appName: '课堂记录助手',
  webDir: 'dist',
  server: {
    url: 'https://class.constfol.cn',
    androidScheme: 'https',
    // 允许 Zitadel 登录域在 App 内导航，否则会被拦截到系统浏览器打开，
    // 导致 OIDC 回调发生在系统浏览器中、丢失 App WebView 内的 session cookie
    allowNavigation: ['auth.constfol.cn'],
  },
}

export default config

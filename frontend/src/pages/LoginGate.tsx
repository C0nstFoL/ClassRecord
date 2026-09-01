import { type ReactNode, useEffect, useState } from 'react'
import { api, type CurrentUser, UnauthorizedError } from '../api'
import ThemeSwitch from './ThemeSwitch'
import { useTheme } from '../useTheme'

interface Props {
  children: (user: CurrentUser) => ReactNode
}

/**
 * 登录门禁：未登录时展示登录入口并跳转到后端 /auth/login，
 * 由后端发起 Zitadel 的 OIDC Authorization Code + PKCE 流程。
 */
export default function LoginGate({ children }: Props) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)
  const { mode, setMode } = useTheme()

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch((err) => {
        if (!(err instanceof UnauthorizedError)) {
          console.error(err)
        }
      })
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <div className="center-box">加载中...</div>
  }

  if (!user) {
    return (
      <div className="login-page">
        <div className="login-theme-switch">
          <ThemeSwitch mode={mode} onChange={setMode} />
        </div>
        <div className="login-card">
          <div className="login-logo">🎓</div>
          <h1>课堂记录助手</h1>
          <p>语音转写 · AI 智能总结 · 内容问答</p>
          <a className="btn primary login-btn" href="/auth/login">
            使用统一身份认证登录
          </a>
        </div>
      </div>
    )
  }

  return <>{children(user)}</>
}

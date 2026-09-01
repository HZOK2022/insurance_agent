import { useState } from "react"
import { login as apiLogin, type LoginResp } from "./lib/api"
import { setToken, setUser, emitAuthChange } from "./lib/auth"

export default function Login() {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [remember, setRemember] = useState(true)
  const [showPw, setShowPw] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (busy) return
    setErr("")
    setBusy(true)
    try {
      const resp: LoginResp = await apiLogin({ username: username.trim(), password, remember })
      setToken(resp.token, remember)
      setUser({ username: resp.username, display_name: resp.display_name })
      emitAuthChange() // Root 监听后切到工作台
    } catch (ex: any) {
      setErr(ex?.message || "登录失败,请重试")
      setBusy(false)
    }
  }

  return (
    <div className="login-root">
      <div className="login-topbar">
        <div className="login-brand">
          <div className="login-logo">保</div>
          <span className="login-name">保险销售知识助手</span>
          <span className="login-badge">内部</span>
        </div>
        <div className="login-env"><span className="login-dot" /> 生产环境</div>
      </div>

      <div className="login-canvas">
        <form className="login-card" onSubmit={submit}>
          <div className="login-brand2">
            <div className="login-logo2">保</div>
            <div>
              <div className="login-name2">保险销售知识助手</div>
              <div className="login-tag2">内部工作台</div>
            </div>
          </div>

          <h1 className="login-title">登录到工作台</h1>

          <div className="login-field">
            <label className="login-label" htmlFor="u">账号</label>
            <input
              id="u" className="login-input" placeholder="请输入账号"
              value={username} autoComplete="username"
              onChange={(e) => setUsername(e.target.value)} />
          </div>

          <div className="login-field">
            <label className="login-label" htmlFor="p">密码</label>
            <div className="login-pw">
              <input
                id="p" className="login-input" type={showPw ? "text" : "password"} placeholder="请输入密码"
                value={password} autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)} />
              <button type="button" className="login-eye" aria-label="显示/隐藏密码"
                onClick={() => setShowPw((s) => !s)}>
                {showPw
                  ? <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><line x1="3" y1="3" x2="21" y2="21"/></svg>
                  : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>}
              </button>
            </div>
          </div>

          <div className="login-row">
            <label className="login-remember">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> 记住我
            </label>
            <a className="login-link" href="#">忘记密码？</a>
          </div>

          {err && <div className="login-err">{err}</div>}

          <button className="login-btn" type="submit" disabled={busy}>
            {busy ? "登录中…" : "登录"}
          </button>

          <div className="login-foot">仅限授权内部人员使用 · v0.0.1</div>
        </form>
      </div>
    </div>
  )
}

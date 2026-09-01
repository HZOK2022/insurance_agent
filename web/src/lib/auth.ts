// 登录态管理:token 存储在 localStorage(记住我)或 sessionStorage(临时)。
// 单一事实键 'api_token',与现有 api.ts authHeaders 衔接。
const KEY = "api_token"
const USER_KEY = "api_user"
export const AUTH_CHANGE = "auth:change"

export interface ApiUser { username: string; display_name: string }

function safeSession(): Storage | null {
  try { return window.sessionStorage } catch { return null }
}

export function getToken(): string {
  if (typeof localStorage === "undefined") return ""
  return localStorage.getItem(KEY) || safeSession()?.getItem(KEY) || ""
}

export function setToken(token: string, remember: boolean): void {
  if (remember) {
    localStorage.setItem(KEY, token)
    safeSession()?.removeItem(KEY)
  } else {
    safeSession()?.setItem(KEY, token)
    localStorage.removeItem(KEY)
  }
}

export function setUser(u: ApiUser): void {
  try {
    const v = JSON.stringify(u)
    localStorage.setItem(USER_KEY, v)
    safeSession()?.setItem(USER_KEY, v)
  } catch { /* 忽略写入失败 */ }
}

export function getUser(): ApiUser | null {
  try {
    const s = localStorage.getItem(USER_KEY) || safeSession()?.getItem(USER_KEY)
    return s ? (JSON.parse(s) as ApiUser) : null
  } catch { return null }
}

export function clearToken(): void {
  localStorage.removeItem(KEY)
  safeSession()?.removeItem(KEY)
  localStorage.removeItem(USER_KEY)
  safeSession()?.removeItem(USER_KEY)
}

export function isAuthed(): boolean {
  return getToken().length > 0
}

export function logout(): void {
  clearToken()
  window.dispatchEvent(new Event(AUTH_CHANGE))
}

// 由登录成功方调用:写 token 后广播,Root 据此切换视图
export function emitAuthChange(): void {
  window.dispatchEvent(new Event(AUTH_CHANGE))
}

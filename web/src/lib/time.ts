// 统一时间工具:全项目用北京时间(UTC+8),格式 YYYY-MM-DD HH:mm:ss。
// 后端返回的时间字符串已是北京时间 "YYYY-MM-DD HH:mm:ss"(无时区后缀)。

const BJ_OFFSET = 8 * 60 // 北京时区偏移(分钟)
const BJ_MS = BJ_OFFSET * 60000

/** Date → 北京时间"墙钟"各字段(y/m/day/hh/mm/ss),**与浏览器本地时区无关**。

 * parseBJ 返回的 Date 是"真实时刻"(= 北京墙钟 -8h),故 +8h 后用 getUTC*() 读出来
 * 即北京墙钟。切勿再掺入 getTimezoneOffset()(那会和 +8h 重复抵消,只在某个时区碰巧对)。 */
export function bjParts(d: Date) {
  const b = new Date(d.getTime() + BJ_MS)
  return {
    y: b.getUTCFullYear(),
    m: b.getUTCMonth() + 1,
    day: b.getUTCDate(),
    hh: b.getUTCHours(),
    mm: b.getUTCMinutes(),
    ss: b.getUTCSeconds(),
  }
}

/** 解析后端返回的时间字符串为 Date 对象(按北京时间解释)。 */
export function parseBJ(s?: string): Date | null {
  if (!s) return null
  // 兼容两种格式:
  // 1. 新格式 "YYYY-MM-DD HH:mm:ss" (北京时间,无时区)
  // 2. 旧格式 ISO8601 (带时区)
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/)
  if (m) {
    // 手动构造北京时间的 UTC 时间戳,避免 new Date() 按本地时区解析
    const utcMs = Date.UTC(
      Number(m[1]), Number(m[2]) - 1, Number(m[3]),
      Number(m[4]) - BJ_OFFSET / 60, Number(m[5]), Number(m[6])
    )
    return new Date(utcMs)
  }
  // 兜底:尝试原生解析
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}

/** 当前北京时间,格式 YYYY-MM-DD HH:mm:ss(不依赖浏览器时区)。 */
export function beijingNow(): string {
  return fmtBJ(new Date())
}

/** 格式化为 YYYY-MM-DD HH:mm:ss(北京时间,不依赖浏览器时区)。 */
export function fmtBJ(d: Date | null): string {
  if (!d) return ""
  const p = bjParts(d)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${p.y}-${pad(p.m)}-${pad(p.day)} ${pad(p.hh)}:${pad(p.mm)}:${pad(p.ss)}`
}

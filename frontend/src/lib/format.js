/** 백엔드는 날짜/일시를 문자열로 내려줍니다. 파싱에 실패하면 원본을 그대로 보여줍니다. */
function toDate(value) {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

const pad = (n) => String(n).padStart(2, '0')

export function formatDateTime(value) {
  const date = toDate(value)
  if (!date) return value ?? '-'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatDate(value) {
  const date = toDate(value)
  if (!date) return value ?? '-'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function formatTime(value) {
  const date = toDate(value)
  if (!date) return ''
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 세션 목록처럼 최근 활동 시각을 짧게 보여줄 때 사용합니다. */
export function formatRelative(value) {
  const date = toDate(value)
  if (!date) return '-'
  const diff = Date.now() - date.getTime()
  if (diff < 0) return formatDateTime(value)
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '방금 전'
  if (minutes < 60) return `${minutes}분 전`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}시간 전`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}일 전`
  return formatDate(value)
}

export function formatNumber(value) {
  if (value === null || value === undefined) return '-'
  const num = Number(value)
  if (Number.isNaN(num)) return String(value)
  return num.toLocaleString('ko-KR')
}

export function formatPercent(value, digits = 1) {
  if (value === null || value === undefined) return '-'
  const num = Number(value)
  if (Number.isNaN(num)) return String(value)
  return `${num.toFixed(digits)}%`
}

export function formatDecimal(value, digits = 2) {
  if (value === null || value === undefined) return '-'
  const num = Number(value)
  if (Number.isNaN(num)) return String(value)
  return num.toFixed(digits)
}

export function formatMillis(value) {
  if (value === null || value === undefined) return '-'
  const num = Number(value)
  if (Number.isNaN(num)) return String(value)
  if (num < 1000) return `${Math.round(num)}ms`
  return `${(num / 1000).toFixed(2)}초`
}

/** `YYYY-MM-DD` 문자열을 만듭니다. (오늘 기준 오프셋 지원) */
export function toDateInput(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function daysAgo(days) {
  const date = new Date()
  date.setDate(date.getDate() - days)
  return toDateInput(date)
}

export function today() {
  return toDateInput(new Date())
}

export function shortId(value) {
  if (!value) return '-'
  return String(value).slice(0, 8)
}

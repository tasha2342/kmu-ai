import { getToken, refreshAccessToken } from './authStore.js'

/**
 * 백엔드 오류 응답(`{ message, target }`)을 담는 예외입니다.
 * 화면에서는 `error.message`와 `error.target`을 그대로 노출해 원인을 파악할 수 있게 합니다.
 */
export class ApiError extends Error {
  constructor(status, body, fallbackMessage) {
    super(body?.message || body?.detail || fallbackMessage || `요청이 실패했습니다. (HTTP ${status})`)
    this.name = 'ApiError'
    this.status = status
    this.body = body ?? null
    this.target = body?.target ?? null
  }
}

/** 백엔드에 아예 닿지 못한 경우(컨테이너 미기동, 네트워크 단절 등)입니다. */
export class NetworkError extends Error {
  constructor(cause) {
    super('백엔드 API에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요.')
    this.name = 'NetworkError'
    this.status = 0
    this.body = null
    this.target = null
    this.cause = cause
  }
}

/** 사람이 읽을 수 있는 오류 문구를 만듭니다. */
export function describeError(error) {
  if (!error) return ''
  if (error.name === 'AbortError') return '요청이 취소되었습니다.'
  const detail = error.target ? ` (${error.target})` : ''
  return `${error.message}${detail}`
}

/** 쿼리 파라미터를 만듭니다. null/undefined/'' 는 제외합니다. */
export function buildQuery(params = {}) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined || value === '') return
    search.append(key, String(value))
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

async function send(path, method, headers, body, rest) {
  try {
    return await fetch(`/v1${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      ...rest,
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new NetworkError(error)
  }
}

/**
 * 백엔드 API 호출 공용 래퍼입니다.
 * `/v1/...` 상대 경로로 호출하며 nginx(운영) / vite(개발)에서 백엔드로 프록시되므로
 * CORS 설정 없이 동일 출처로 통신합니다.
 * 토큰은 authStore 한 곳에서만 관리하며, 401을 받으면 한 번 갱신 후 재시도합니다.
 */
export async function apiFetch(path, {
  method = 'GET',
  body,
  auth = true,
  formData,
  timeoutMs,
  signal,
  ...rest
} = {}) {
  const headers = { ...rest.headers }
  if (body !== undefined && !formData) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }

  // 호출측 AbortSignal과 타임아웃을 합친다. 업로드가 무한히 '업로드 중…'에 남지 않게 한다.
  const timeoutController = typeof timeoutMs === 'number' && timeoutMs > 0 ? new AbortController() : null
  const timeoutId = timeoutController
    ? setTimeout(() => timeoutController.abort(), timeoutMs)
    : null
  const combinedSignal = mergeAbortSignals(signal, timeoutController?.signal)

  const doFetch = async (fetchHeaders) => {
    try {
      return await fetch(`/v1${path}`, {
        method,
        headers: fetchHeaders,
        body: formData ? formData : body !== undefined ? JSON.stringify(body) : undefined,
        ...rest,
        signal: combinedSignal,
      })
    } catch (error) {
      if (error?.name === 'AbortError') {
        if (timeoutController?.signal?.aborted && !signal?.aborted) {
          throw new ApiError(0, null, '요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.')
        }
        throw error
      }
      throw new NetworkError(error)
    }
  }

  try {
    let response = await doFetch(headers)

    if (response.status === 401 && auth) {
      const refreshed = await refreshAccessToken()
      if (refreshed?.access_token) {
        headers['Authorization'] = `Bearer ${refreshed.access_token}`
        response = await doFetch(headers)
      }
    }

    let data = null
    try {
      data = await response.json()
    } catch {
      data = null
    }

    if (!response.ok) {
      throw new ApiError(response.status, data, defaultMessageFor(response.status))
    }

    return data
  } finally {
    if (timeoutId) clearTimeout(timeoutId)
  }
}

/** 여러 AbortSignal 중 하나라도 abort되면 같이 중단되는 signal을 만듭니다. */
function mergeAbortSignals(...signals) {
  const active = signals.filter(Boolean)
  if (active.length === 0) return undefined
  if (active.length === 1) return active[0]
  if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.any === 'function') {
    return AbortSignal.any(active)
  }
  const controller = new AbortController()
  for (const item of active) {
    if (item.aborted) {
      controller.abort(item.reason)
      return controller.signal
    }
    item.addEventListener('abort', () => controller.abort(item.reason), { once: true })
  }
  return controller.signal
}

function defaultMessageFor(status) {
  if (status === 401) return '인증이 필요합니다. 다시 로그인해주세요.'
  if (status === 403) return '접근 권한이 없습니다.'
  if (status === 404) return '요청한 항목을 찾을 수 없습니다.'
  if (status === 422) return '요청 형식이 올바르지 않습니다.'
  if (status === 503) return '서비스를 일시적으로 사용할 수 없습니다. (모델 미등록 또는 색인 미완료일 수 있습니다)'
  if (status >= 500) return '서버 오류가 발생했습니다.'
  return undefined
}

export const api = {
  get: (path, options) => apiFetch(path, { ...options, method: 'GET' }),
  post: (path, body, options) => apiFetch(path, { ...options, method: 'POST', body }),
  postForm: (path, formData, options) => apiFetch(path, { ...options, method: 'POST', formData }),
  patch: (path, body, options) => apiFetch(path, { ...options, method: 'PATCH', body }),
  delete: (path, options) => apiFetch(path, { ...options, method: 'DELETE' }),
}

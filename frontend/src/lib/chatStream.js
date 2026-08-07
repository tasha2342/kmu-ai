import { ApiError, NetworkError } from './api.js'
import { getToken } from './authStore.js'

// 토큰이 도착할 때마다 매번 React 렌더링(+마크다운 재파싱)을 하면 응답이 길어질수록
// 버벅임이 누적되므로, 짧은 주기로 모아서 한 번에 전달합니다.
const FLUSH_INTERVAL_MS = 40

/**
 * `POST /v1/chatbot/message` 의 SSE 스트림을 처리합니다.
 *
 * `EventSource`는 Authorization 헤더를 붙일 수 없어 사용할 수 없습니다.
 * 그래서 `fetch` + `ReadableStream`으로 직접 SSE 프레임을 파싱합니다.
 *
 * 백엔드가 보내는 이벤트 (app/api/v1/endpoints/chat.py)
 *   - `session` : { session_id, user_message_id, message_id, detected_intent, language, notice }
 *   - `sources` : { sources: ChatSourceItem[] }
 *   - `delta`   : { content }
 *   - `error`   : { message }
 *   - `done`    : { session_id, message_id, detected_intent, is_answered, unanswered_reason, latency_ms }
 *
 * @param {object} payload  { message, session_id?, language?, attachments? }
 * @param {object} handlers { onSession, onSources, onDelta, onError, onDone }
 * @param {AbortSignal} [signal]
 */
export async function streamChatMessage(payload, handlers = {}, signal) {
  const { onSession, onSources, onDelta, onError, onDone } = handlers

  const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  let response
  try {
    response = await fetch('/v1/chatbot/message', {
      method: 'POST',
      headers,
      body: JSON.stringify({ ...payload, stream: true }),
      signal,
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new NetworkError(error)
  }

  if (!response.ok) {
    // 스트리밍 시작 전에 실패하면 일반 JSON 오류 응답(`{message, target}`)이 옵니다.
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, body)
  }
  if (!response.body) {
    throw new ApiError(response.status, null, '스트리밍 응답을 읽을 수 없는 브라우저입니다.')
  }

  // ---- 델타 버퍼링 ----
  let pending = ''
  let flushTimer = null

  const flush = () => {
    flushTimer = null
    if (!pending) return
    const chunk = pending
    pending = ''
    onDelta?.(chunk)
  }
  const scheduleFlush = () => {
    if (flushTimer) return
    flushTimer = setTimeout(flush, FLUSH_INTERVAL_MS)
  }

  const dispatch = (eventName, dataStr) => {
    if (!dataStr) return
    let data
    try {
      data = JSON.parse(dataStr)
    } catch {
      return
    }

    switch (eventName) {
      case 'session':
        onSession?.(data)
        break
      case 'sources':
        onSources?.(data.sources ?? [])
        break
      case 'delta':
        if (data.content) {
          pending += data.content
          scheduleFlush()
        }
        break
      case 'error':
        flush()
        onError?.(data)
        break
      case 'done':
        flush()
        onDone?.(data)
        break
      default:
        break
    }
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // SSE 프레임 구분자는 빈 줄입니다. (CRLF 환경도 함께 처리)
      let separator
      while ((separator = findFrameEnd(buffer)) !== -1) {
        const frame = buffer.slice(0, separator.index)
        buffer = buffer.slice(separator.index + separator.length)
        parseFrame(frame, dispatch)
      }
    }
    // 마지막 프레임이 구분자 없이 끝난 경우도 처리합니다.
    if (buffer.trim()) parseFrame(buffer, dispatch)
  } finally {
    if (flushTimer) clearTimeout(flushTimer)
    flush()
    try {
      reader.releaseLock()
    } catch {
      // 이미 해제된 경우 무시합니다.
    }
  }
}

function findFrameEnd(buffer) {
  const lf = buffer.indexOf('\n\n')
  const crlf = buffer.indexOf('\r\n\r\n')
  if (lf === -1 && crlf === -1) return -1
  if (crlf !== -1 && (lf === -1 || crlf < lf)) return { index: crlf, length: 4 }
  return { index: lf, length: 2 }
}

function parseFrame(frame, dispatch) {
  let eventName = 'message'
  const dataLines = []

  for (const rawLine of frame.split(/\r?\n/)) {
    const line = rawLine.trimEnd()
    if (!line) continue
    // `:` 로 시작하면 주석(keep-alive ping)입니다.
    if (line.startsWith(':')) continue

    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') eventName = value
    else if (field === 'data') dataLines.push(value)
  }

  if (dataLines.length === 0) return
  dispatch(eventName, dataLines.join('\n'))
}

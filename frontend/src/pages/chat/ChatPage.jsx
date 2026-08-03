import { useCallback, useEffect, useRef, useState } from 'react'
import ChatSessionList from './ChatSessionList.jsx'
import ChatMarkdown from '../../components/ChatMarkdown.jsx'
import ChatSources from '../../components/ChatSources.jsx'
import MessageFeedback from '../../components/MessageFeedback.jsx'
import AuthPanel from '../../components/AuthPanel.jsx'
import { Button, ErrorNotice, InlineNotice, Select, Spinner } from '../../components/ui.jsx'
import { chatbot } from '../../lib/endpoints.js'
import { streamChatMessage } from '../../lib/chatStream.js'
import { useAuth } from '../../lib/useAuth.js'
import { CHAT_INTENT_LABEL, LANGUAGES, SAMPLE_QUESTIONS } from '../../lib/constants.js'
import { formatMillis, formatRelative, formatTime } from '../../lib/format.js'

/**
 * 자동으로 이어받을 수 있는 마지막 활동 시각의 상한(분)입니다. (KAI-REQ-041 세션 기억)
 *
 * 서버는 `SESSION_IDLE_TIMEOUT_MINUTES`(기본 30분)를 넘긴 세션을 `idle_closed`로 바꾸므로(KAI-REQ-039)
 * 오래된 세션은 대부분 아래 `status === 'active'` 검사에서 걸러집니다.
 * 그럼에도 상한을 따로 두는 이유는 두 가지입니다.
 *  1. `ENABLE_SESSION_IDLE_TIMEOUT=false`면 세션이 계속 `active`로 남습니다. 이때 며칠 전 대화를
 *     아무 말 없이 이어받으면 사용자가 지금 대화의 맥락을 오해합니다.
 *  2. 자동 종료 스케줄러는 5분 주기라, 유휴 만료 직후~다음 실행 사이에는 목록에 아직 `active`로 보입니다.
 *
 * 서버 기준 시간은 프론트가 알 수 없으므로 "같은 날 안에 돌아온 경우"까지만 이어받기로 하고 12시간을 씁니다.
 * 서버 기준이 이보다 짧으면 서버가 먼저 세션을 종료시키므로, 이 값이 서버 정책을 느슨하게 만들지는 않습니다.
 */
const RESUME_MAX_IDLE_MINUTES = 12 * 60

/**
 * 세션 목록에서 자동으로 이어받을 세션을 고릅니다. 대상이 없으면 null입니다.
 *
 * 목록 정렬(`updated_at_desc`)은 제목 수정·상태 변경으로도 바뀌므로,
 * 실제 대화 시각인 `last_active_at`으로 다시 고릅니다.
 */
function pickResumableSession(sessions) {
  const oldestAllowed = Date.now() - RESUME_MAX_IDLE_MINUTES * 60000
  let picked = null
  let pickedAt = 0

  for (const session of sessions ?? []) {
    // 종료된 세션(closed / idle_closed)은 제외합니다. 서버가 종료된 세션으로의 전송을 400으로 막기 때문에
    // 복원해도 대화를 이어갈 수 없습니다. (KAI-REQ-039)
    if (session?.status !== 'active') continue
    // 메시지가 없는 세션은 이어받을 맥락이 없어 새 대화와 다르지 않습니다.
    if ((session.message_count ?? 0) <= 0) continue

    const lastActiveAt = new Date(session.last_active_at ?? 0).getTime()
    if (!Number.isFinite(lastActiveAt) || lastActiveAt < oldestAllowed) continue
    if (lastActiveAt > pickedAt) {
      picked = session
      pickedAt = lastActiveAt
    }
  }

  return picked
}

/** 대화 화면. 좌측 세션 목록 + 우측 대화창으로 구성됩니다. */
export default function ChatPage() {
  const session = useAuth()
  const hasToken = !!session

  const [sessions, setSessions] = useState([])
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [sessionsError, setSessionsError] = useState(null)

  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [historyError, setHistoryError] = useState(null)
  const [historyLoading, setHistoryLoading] = useState(false)

  const [language, setLanguage] = useState('ko')
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [sendError, setSendError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [feedbackGiven, setFeedbackGiven] = useState({})
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [authPanelOpen, setAuthPanelOpen] = useState(false)

  // 자동으로 이어받은 세션. 이어받았다는 사실을 사용자에게 알리는 안내에만 씁니다.
  const [resumedSession, setResumedSession] = useState(null)
  // 이어받을 세션을 찾는 중. 이 동안 "무엇을 도와드릴까요?" 안내가 깜빡이는 것을 막습니다.
  const [resumeChecking, setResumeChecking] = useState(false)

  const abortRef = useRef(null)
  const scrollRef = useRef(null)
  const textareaRef = useRef(null)
  // 로그인 1회당 자동 복원은 한 번만 시도합니다. (메시지 전송 후의 목록 갱신 등으로 다시 끼어들면 안 됨)
  const autoResumeDoneRef = useRef(false)
  // 목록을 기다리는 사이에 사용자가 먼저 조작했으면 자동 복원을 포기합니다.
  const userActedRef = useRef(false)
  // 대화 이력 요청 순번. 늦게 도착한 응답이 화면을 덮어쓰는 것을 막습니다.
  const historySeqRef = useRef(0)

  // ===== 세션 목록 =====

  /** 세션 목록을 다시 불러오고, 불러온 항목을 그대로 반환합니다. (자동 복원이 이 결과를 씁니다) */
  const loadSessions = useCallback(async () => {
    if (!hasToken) {
      setSessions([])
      setSessionsError(null)
      return []
    }
    setSessionsLoading(true)
    try {
      const data = await chatbot.listSessions({ page: 1, count: 50, order_by: 'updated_at_desc' })
      const items = data?.items ?? []
      setSessions(items)
      setSessionsError(null)
      return items
    } catch (error) {
      setSessions([])
      setSessionsError(error)
      return []
    } finally {
      setSessionsLoading(false)
    }
  }, [hasToken])

  // ===== 스크롤 =====

  useEffect(() => {
    const element = scrollRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [messages])

  // ===== 대화 불러오기 =====

  // 자동 복원 effect가 의존하므로 렌더마다 새로 만들지 않습니다. (setter와 chatbot만 쓰므로 의존성이 없습니다)
  const selectSession = useCallback(async (target) => {
    // 자동 복원이 진행되는 중에 사용자가 새 대화를 시작하거나 다른 세션을 고를 수 있습니다.
    // 늦게 도착한 이력이 그 화면을 덮어쓰지 않도록 마지막 요청만 반영합니다.
    const seq = ++historySeqRef.current

    setSidebarOpen(false)
    setSendError(null)
    setNotice(null)
    setActiveId(target.id)
    setLanguage(target.language ?? 'ko')
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      const data = await chatbot.getSession(target.id, { message_limit: 0 })
      if (seq !== historySeqRef.current) return
      setMessages(
        (data?.messages ?? []).map((message) => ({
          id: message.id,
          role: message.role,
          content: message.content,
          sources: message.sources ?? [],
          detected_intent: message.detected_intent,
          is_answered: message.is_answered,
          latency_ms: message.latency_ms,
          created_at: message.created_at,
        }))
      )
    } catch (error) {
      if (seq !== historySeqRef.current) return
      setMessages([])
      setHistoryError(error)
    } finally {
      // 뒤이은 요청이 이미 로딩 중이라면 그쪽이 끝날 때 내립니다.
      if (seq === historySeqRef.current) setHistoryLoading(false)
    }
  }, [])

  /** 사용자가 목록에서 직접 고른 경우. 이후로는 자동 복원이 끼어들지 않습니다. */
  const handleSelectSession = (target) => {
    userActedRef.current = true
    setResumedSession(null)
    return selectSession(target)
  }

  // ===== 이전 대화 자동 이어받기 (KAI-REQ-041 세션 기억) =====
  // 백엔드는 세션 요약(summary)으로 장기 맥락을 유지하지만, 진입할 때마다 새 대화로 시작하면 그 요약이 쓰이지 않습니다.
  // 그래서 로그인 후 처음 들어오면 가장 최근 활성 세션을 자동으로 복원합니다.
  useEffect(() => {
    if (!hasToken) {
      // 로그아웃하면 다음 로그인에서 다시 이어받을 수 있도록 되돌립니다.
      autoResumeDoneRef.current = false
      userActedRef.current = false
      setResumedSession(null)
      setResumeChecking(false)
      loadSessions()
      return
    }

    let cancelled = false
    const shouldResume = !autoResumeDoneRef.current && !userActedRef.current
    if (shouldResume) setResumeChecking(true)

    ;(async () => {
      const items = await loadSessions()
      // 목록을 기다리는 사이에 화면을 벗어났거나(언마운트) 사용자가 먼저 조작했으면 손대지 않습니다.
      if (cancelled) return
      if (!shouldResume || userActedRef.current) {
        setResumeChecking(false)
        return
      }
      autoResumeDoneRef.current = true

      const target = pickResumableSession(items)
      if (!target) {
        setResumeChecking(false)
        return
      }

      await selectSession(target)
      setResumeChecking(false)
      // 이력을 불러오는 사이에 사용자가 새 대화를 시작했다면 이어받기 안내를 띄우지 않습니다.
      if (cancelled || userActedRef.current) return
      setResumedSession(target)
    })()

    return () => {
      cancelled = true
    }
  }, [hasToken, loadSessions, selectSession])

  const startNewChat = () => {
    // 자동 복원이 새 대화 시작을 막지 않도록, 사용자가 명시적으로 시작하면 항상 빈 화면으로 되돌립니다.
    userActedRef.current = true
    setResumedSession(null)
    // 자동 복원 등으로 진행 중인 이력 요청이 있으면 그 결과를 버립니다.
    historySeqRef.current += 1
    abortRef.current?.abort()
    setActiveId(null)
    setMessages([])
    setSendError(null)
    setHistoryError(null)
    setHistoryLoading(false)
    setNotice(null)
    setSidebarOpen(false)
    textareaRef.current?.focus()
  }

  const renameSession = async (target, title) => {
    try {
      await chatbot.updateSession(target.id, { title })
      setSessions((prev) => prev.map((item) => (item.id === target.id ? { ...item, title } : item)))
    } catch (error) {
      setSessionsError(error)
    }
  }

  const deleteSession = async (target) => {
    if (!window.confirm('이 대화를 삭제할까요? 삭제하면 되돌릴 수 없습니다.')) return
    try {
      await chatbot.deleteSession(target.id)
      setSessions((prev) => prev.filter((item) => item.id !== target.id))
      if (target.id === activeId) startNewChat()
    } catch (error) {
      setSessionsError(error)
    }
  }

  // ===== 메시지 전송 =====

  const send = async (rawText) => {
    const text = (rawText ?? input).trim()
    if (!text || streaming) return

    if (!hasToken) {
      setSendError({
        status: 401,
        message: '로그인이 필요합니다. 우측 상단의 로그인 버튼을 눌러주세요.',
        target: null,
      })
      return
    }

    // 이 시점부터는 사용자가 대화를 시작한 것이므로 자동 복원이 끼어들지 않습니다.
    // 이어받기 안내도 내립니다. 유휴로 세션이 자동 종료되면 서버가 새 세션을 만들어 주므로(KAI-REQ-039)
    // 안내를 남겨두면 이미 끊긴 대화를 이어가는 중이라고 잘못 읽힐 수 있습니다.
    userActedRef.current = true
    setResumedSession(null)
    // 진행 중인 이력 요청이 방금 보낸 메시지를 덮어쓰지 않게 합니다.
    historySeqRef.current += 1
    setHistoryLoading(false)

    setInput('')
    setSendError(null)
    setNotice(null)

    const localUserId = `local-user-${Date.now()}`
    const localBotId = `local-bot-${Date.now()}`

    setMessages((prev) => [
      ...prev,
      { id: localUserId, role: 'user', content: text, created_at: new Date().toISOString() },
      { id: localBotId, role: 'assistant', content: '', sources: [], streaming: true },
    ])
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller

    // 세션이 없으면 먼저 생성해 언어 설정을 반영합니다.
    // 생성에 실패해도 대화가 막히지 않도록, 세션 없이(자동 생성) 전송을 계속합니다.
    let sessionId = activeId
    if (!sessionId) {
      try {
        const created = await chatbot.createSession({ language })
        if (created?.id) {
          sessionId = created.id
          setActiveId(created.id)
          setSessions((prev) => [created, ...prev])
        }
      } catch {
        sessionId = null
      }
    }

    const patchBot = (updater) =>
      setMessages((prev) => prev.map((message) => (message.id === localBotId ? { ...message, ...updater(message) } : message)))

    try {
      await streamChatMessage(
        { message: text, session_id: sessionId ?? undefined, language },
        {
          onSession: (data) => {
            if (data.session_id) {
              // 유휴로 이전 세션이 자동 종료되면 서버가 새 세션을 만들어 답변합니다. (KAI-REQ-039)
              // 이때 화면에 남아 있는 이전 세션의 대화는 새 세션의 이력이 아니므로 방금 보낸 말풍선만 남깁니다.
              if (sessionId && data.session_id !== sessionId) {
                setMessages((prev) =>
                  prev.filter((message) => message.id === localUserId || message.id === localBotId)
                )
              }
              sessionId = data.session_id
              setActiveId(data.session_id)
            }
            if (data.notice) setNotice(data.notice)
            patchBot(() => ({
              serverId: data.message_id,
              detected_intent: data.detected_intent,
            }))
            setMessages((prev) =>
              prev.map((message) =>
                message.id === localUserId ? { ...message, serverId: data.user_message_id } : message
              )
            )
          },
          onSources: (sources) => patchBot(() => ({ sources })),
          onDelta: (chunk) => patchBot((message) => ({ content: message.content + chunk })),
          onError: (data) => patchBot(() => ({ streamError: data?.message || '응답 생성 중 오류가 발생했습니다.' })),
          onDone: (data) =>
            patchBot(() => ({
              streaming: false,
              id: data.message_id || localBotId,
              serverId: data.message_id,
              is_answered: data.is_answered,
              unanswered_reason: data.unanswered_reason,
              detected_intent: data.detected_intent,
              latency_ms: data.latency_ms,
              created_at: new Date().toISOString(),
            })),
        },
        controller.signal
      )
    } catch (error) {
      if (error?.name === 'AbortError') {
        patchBot((message) => ({
          streaming: false,
          content: message.content || '(응답이 중단되었습니다)',
        }))
      } else {
        setSendError(error)
        // 실패한 빈 응답 말풍선은 남겨두지 않습니다.
        setMessages((prev) => prev.filter((message) => !(message.id === localBotId && !message.content)))
        setMessages((prev) => prev.map((m) => (m.id === localBotId ? { ...m, streaming: false } : m)))
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
      patchBot(() => ({ streaming: false }))
      loadSessions()
    }
  }

  const stop = () => abortRef.current?.abort()

  const onKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      send()
    }
  }

  const showIntro = messages.length === 0 && !historyLoading && !resumeChecking

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] overflow-hidden">
      {/* ===== 좌측 세션 목록 ===== */}
      <aside className="hidden w-72 shrink-0 border-r border-gray-200 bg-white lg:block">
        <ChatSessionList
          sessions={sessions}
          loading={sessionsLoading}
          error={sessionsError}
          activeId={activeId}
          hasToken={hasToken}
          onSelect={handleSelectSession}
          onNewChat={startNewChat}
          onRename={renameSession}
          onDelete={deleteSession}
          onReload={loadSessions}
        />
      </aside>

      {/* 모바일 드로어 */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setSidebarOpen(false)} />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85vw] bg-white shadow-xl">
            <ChatSessionList
              sessions={sessions}
              loading={sessionsLoading}
              error={sessionsError}
              activeId={activeId}
              hasToken={hasToken}
              onSelect={handleSelectSession}
              onNewChat={startNewChat}
              onRename={renameSession}
              onDelete={deleteSession}
              onReload={loadSessions}
            />
          </div>
        </div>
      )}

      {/* ===== 우측 대화창 ===== */}
      <main className="flex min-w-0 flex-1 flex-col bg-gray-50">
        <div className="flex items-center gap-2 border-b border-gray-200 bg-white px-3 py-2 sm:px-5">
          <Button variant="secondary" size="sm" className="lg:hidden" onClick={() => setSidebarOpen(true)}>
            대화 목록
          </Button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-gray-900">
              {sessions.find((item) => item.id === activeId)?.title || '새 대화'}
            </p>
          </div>
          <Select
            value={language}
            onChange={(event) => setLanguage(event.target.value)}
            disabled={streaming}
            className="w-auto py-1.5 text-xs"
            aria-label="응답 언어"
          >
            {LANGUAGES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </div>

        {/* 이어받았다는 사실은 스크롤에 묻히면 안 되므로 대화창 위에 고정해 보여줍니다. */}
        {resumedSession && (
          <div className="border-b border-gray-200 bg-white px-3 pb-2 sm:px-5">
            <InlineNotice tone="info">
              이전 대화를 이어서 표시했습니다. (마지막 활동 {formatRelative(resumedSession.last_active_at)}) 새로
              시작하려면 ‘새 대화 시작’을 눌러주세요.
            </InlineNotice>
          </div>
        )}

        <div ref={scrollRef} className="scrollbar-thin flex-1 overflow-y-auto px-3 py-4 sm:px-5">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
            {historyLoading && (
              <div className="flex justify-center py-10 text-xs text-gray-400">
                <Spinner className="mr-2 h-4 w-4" /> 대화를 불러오는 중입니다…
              </div>
            )}

            {historyError && <ErrorNotice error={historyError} onRetry={() => selectSession({ id: activeId })} />}

            {showIntro && <Intro hasToken={hasToken} onPick={(question) => send(question)} onOpenLogin={() => setAuthPanelOpen(true)} />}

            {messages.map((message) =>
              message.role === 'user' ? (
                <UserBubble key={message.id} message={message} />
              ) : (
                <BotBubble
                  key={message.id}
                  message={message}
                  feedbackGiven={feedbackGiven}
                  onFeedback={(id, rating) => setFeedbackGiven((prev) => ({ ...prev, [id]: rating }))}
                />
              )
            )}

            {notice && <InlineNotice tone="info">{notice}</InlineNotice>}
            {sendError && <ErrorNotice error={sendError} />}
          </div>
        </div>

        <div className="border-t border-gray-200 bg-white px-3 py-3 sm:px-5">
          <div className="mx-auto w-full max-w-3xl">
            <div className="flex items-end gap-2 rounded-xl border border-gray-300 bg-white px-3 py-2 focus-within:border-kmu-600 focus-within:ring-1 focus-within:ring-kmu-600">
              <textarea
                ref={textareaRef}
                rows={1}
                value={input}
                maxLength={4000}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder="학사·장학·취업 등 궁금한 점을 물어보세요."
                className="max-h-40 min-h-[1.5rem] flex-1 resize-none bg-transparent text-sm leading-6 text-gray-900 outline-none placeholder:text-gray-400"
              />
              {streaming ? (
                <Button variant="secondary" size="sm" onClick={stop}>
                  중지
                </Button>
              ) : (
                <Button size="sm" onClick={() => send()} disabled={!input.trim()}>
                  전송
                </Button>
              )}
            </div>
            <p className="mt-1.5 text-[11px] text-gray-400">
              Enter로 전송, Shift+Enter로 줄바꿈 · AI가 생성한 답변이므로 중요한 사항은 담당 부서에 확인해주세요.
            </p>
          </div>
        </div>
      </main>

      <AuthPanel open={authPanelOpen} onClose={() => setAuthPanelOpen(false)} />
    </div>
  )
}

function Intro({ hasToken, onPick, onOpenLogin }) {
  return (
    <div className="py-6">
      <h1 className="text-lg font-semibold text-gray-900">무엇을 도와드릴까요?</h1>
      <p className="mt-1 text-sm text-gray-500">
        계명대학교 학사·장학·취업 정보를 안내합니다. 등록된 FAQ와 학내 자료를 근거로 답변합니다.
      </p>

      {!hasToken && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-relaxed text-amber-800">
          <p className="font-medium">로그인이 필요합니다.</p>
          <p className="mt-1">계명대학교 통합 인증으로 로그인하면 질문을 보내고 대화 기록을 이어갈 수 있습니다.</p>
          <Button variant="secondary" size="sm" className="mt-2" onClick={onOpenLogin}>
            로그인
          </Button>
        </div>
      )}

      <div className="mt-5 grid gap-2 sm:grid-cols-2">
        {SAMPLE_QUESTIONS.map((question) => (
          <button
            key={question}
            type="button"
            onClick={() => onPick(question)}
            className="rounded-lg border border-gray-200 bg-white px-3.5 py-3 text-left text-xs text-gray-600 transition-colors hover:border-kmu-300 hover:bg-kmu-50 hover:text-kmu-900"
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  )
}

function UserBubble({ message }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-kmu-800 px-4 py-2.5 text-sm leading-relaxed text-white sm:max-w-[75%]">
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
    </div>
  )
}

function BotBubble({ message, feedbackGiven, onFeedback }) {
  const messageId = message.serverId ?? (isUuid(message.id) ? message.id : null)

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[95%] sm:max-w-[85%]">
        <div className="rounded-2xl rounded-bl-md border border-gray-200 bg-white px-4 py-3">
          {message.content ? (
            <ChatMarkdown className="text-sm text-gray-800">{message.content}</ChatMarkdown>
          ) : message.streaming ? (
            <ThinkingDots />
          ) : (
            <p className="text-sm text-gray-400">응답 내용이 없습니다.</p>
          )}

          {message.streamError && (
            <p className="mt-2 rounded-md bg-rose-50 px-2.5 py-1.5 text-[11px] text-rose-700">{message.streamError}</p>
          )}

          {!message.streaming && (
            <ChatSources
              sources={message.sources}
              isAnswered={message.is_answered}
              unansweredReason={message.unanswered_reason}
            />
          )}
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 px-1 text-[11px] text-gray-400">
          {message.detected_intent && <span>{CHAT_INTENT_LABEL[message.detected_intent] ?? message.detected_intent}</span>}
          {message.latency_ms ? <span>· {formatMillis(message.latency_ms)}</span> : null}
          {message.created_at && <span>· {formatTime(message.created_at)}</span>}
        </div>

        {!message.streaming && messageId && (
          <div className="px-1">
            <MessageFeedback
              messageId={messageId}
              submitted={feedbackGiven[messageId]}
              onSubmitted={(rating) => onFeedback(messageId, rating)}
            />
          </div>
        )}
      </div>
    </div>
  )
}

function ThinkingDots() {
  return (
    <div className="flex items-center gap-1 py-1" aria-label="답변을 생성하는 중입니다">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-300"
          style={{ animationDelay: `${index * 0.15}s` }}
        />
      ))}
    </div>
  )
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
function isUuid(value) {
  return typeof value === 'string' && UUID_RE.test(value)
}

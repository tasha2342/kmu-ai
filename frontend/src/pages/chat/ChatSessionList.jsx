import { useState } from 'react'
import { CHAT_LANGUAGE_LABEL, CHAT_SESSION_STATUS_LABEL } from '../../lib/constants.js'
import { formatRelative } from '../../lib/format.js'
import { Button, EmptyState, ErrorNotice, Input, SkeletonRows } from '../../components/ui.jsx'

/** 좌측 대화 세션 목록입니다. 제목 수정·삭제·새 대화 시작을 담당합니다. */
export default function ChatSessionList({
  sessions = [],
  loading,
  error,
  activeId,
  hasToken,
  onSelect,
  onNewChat,
  onRename,
  onDelete,
  onReload,
}) {
  const [editingId, setEditingId] = useState(null)
  const [draftTitle, setDraftTitle] = useState('')

  const startRename = (session) => {
    setEditingId(session.id)
    setDraftTitle(session.title ?? '')
  }

  const commitRename = async (session) => {
    const title = draftTitle.trim()
    setEditingId(null)
    if (!title || title === session.title) return
    await onRename?.(session, title)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-gray-200 px-3 py-3">
        <Button className="w-full" onClick={onNewChat}>
          + 새 대화 시작
        </Button>
      </div>

      <div className="scrollbar-thin flex-1 overflow-y-auto px-2 py-2">
        {loading && <SkeletonRows rows={4} className="px-1" />}

        {!loading && error && (
          <ErrorNotice
            error={error}
            onRetry={onReload}
            className="mx-1 text-xs"
          />
        )}

        {!loading && !error && !hasToken && (
          <p className="px-2 py-6 text-center text-xs leading-relaxed text-gray-400">
            로그인하면
            <br />
            지난 대화 목록을 볼 수 있습니다.
          </p>
        )}

        {!loading && !error && hasToken && sessions.length === 0 && (
          <EmptyState
            title="대화 기록이 없습니다"
            description="질문을 보내면 자동으로 세션이 만들어집니다."
            className="mx-1 border-0 px-2 py-8"
          />
        )}

        <ul className="space-y-0.5">
          {sessions.map((session) => {
            const active = session.id === activeId
            return (
              <li key={session.id}>
                <div
                  className={`group rounded-lg px-2.5 py-2 transition-colors ${
                    active ? 'bg-kmu-50' : 'hover:bg-gray-100'
                  }`}
                >
                  {editingId === session.id ? (
                    <Input
                      autoFocus
                      value={draftTitle}
                      maxLength={255}
                      onChange={(event) => setDraftTitle(event.target.value)}
                      onBlur={() => commitRename(session)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') commitRename(session)
                        if (event.key === 'Escape') setEditingId(null)
                      }}
                      className="py-1 text-xs"
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={() => onSelect?.(session)}
                      className="block w-full text-left"
                    >
                      <span
                        className={`block truncate text-xs font-medium ${active ? 'text-kmu-900' : 'text-gray-700'}`}
                      >
                        {session.title || '제목 없는 대화'}
                      </span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-gray-400">
                        <span>{formatRelative(session.last_active_at)}</span>
                        <span>·</span>
                        <span>{session.message_count ?? 0}개</span>
                        {/* 기본값(자동 감지)과 한국어는 표시하지 않습니다. 모든 세션에 붙어 정보가 되지 않습니다. */}
                        {session.language && session.language !== 'ko' && session.language !== 'auto' && (
                          <>
                            <span>·</span>
                            <span>{CHAT_LANGUAGE_LABEL[session.language] ?? session.language}</span>
                          </>
                        )}
                        {session.status && session.status !== 'active' && (
                          <>
                            <span>·</span>
                            <span>{CHAT_SESSION_STATUS_LABEL[session.status] ?? session.status}</span>
                          </>
                        )}
                      </span>
                    </button>
                  )}

                  {editingId !== session.id && (
                    <div className="mt-1 flex gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                      <button
                        type="button"
                        onClick={() => startRename(session)}
                        className="rounded px-1.5 py-0.5 text-[10px] text-gray-500 hover:bg-white hover:text-gray-800"
                      >
                        제목 수정
                      </button>
                      <button
                        type="button"
                        onClick={() => onDelete?.(session)}
                        className="rounded px-1.5 py-0.5 text-[10px] text-gray-500 hover:bg-white hover:text-rose-600"
                      >
                        삭제
                      </button>
                    </div>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}

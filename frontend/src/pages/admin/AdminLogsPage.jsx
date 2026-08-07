import { useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Input,
  Pagination,
  Select,
  SkeletonRows,
} from '../../components/ui.jsx'
import { chatbotAdmin } from '../../lib/endpoints.js'
import { useApiResource } from '../../lib/useApiResource.js'
import { useAuth } from '../../lib/useAuth.js'
import { CHAT_INTENT_LABEL, CHAT_ROLE_LABEL } from '../../lib/constants.js'
import { daysAgo, formatDateTime, formatDecimal, formatMillis, formatNumber, shortId, today } from '../../lib/format.js'

const PAGE_SIZE = 20

const TABS = [
  { key: 'conversation', label: '대화 이력' },
  { key: 'retrieval', label: '검색 로그' },
  { key: 'user', label: '사용자별 이용' },
]

/** 챗봇 로그 조회 화면입니다. (KAI-REQ-044 / 045) */
export default function AdminLogsPage() {
  const session = useAuth()
  const enabled = !!session
  const [tab, setTab] = useState('conversation')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1">
        {TABS.map((item) => (
          <Button key={item.key} variant={tab === item.key ? 'subtle' : 'ghost'} size="sm" onClick={() => setTab(item.key)}>
            {item.label}
          </Button>
        ))}
      </div>

      {tab === 'conversation' && <ConversationLogs enabled={enabled} />}
      {tab === 'retrieval' && <RetrievalLogs enabled={enabled} />}
      {tab === 'user' && <UserLogs enabled={enabled} />}
    </div>
  )
}

function FilterBar({ children }) {
  return <div className="mb-3 flex flex-wrap items-end gap-2">{children}</div>
}

function DateFilters({ filters, onChange }) {
  return (
    <>
      <label className="text-xs text-gray-500">
        <span className="mb-1 block">시작일</span>
        <Input
          type="date"
          value={filters.start_date}
          onChange={(event) => onChange({ start_date: event.target.value })}
          className="w-auto py-1.5 text-xs"
        />
      </label>
      <label className="text-xs text-gray-500">
        <span className="mb-1 block">종료일</span>
        <Input
          type="date"
          value={filters.end_date}
          onChange={(event) => onChange({ end_date: event.target.value })}
          className="w-auto py-1.5 text-xs"
        />
      </label>
    </>
  )
}

function TableShell({ resource, page, setPage, columns, children, emptyTitle }) {
  const items = resource.data?.items ?? []
  return (
    <>
      {resource.loading && <SkeletonRows rows={6} />}
      {!resource.loading && resource.error && <ErrorNotice error={resource.error} onRetry={resource.reload} />}
      {!resource.loading && !resource.error && items.length === 0 && (
        <EmptyState title={emptyTitle} description="선택한 조건에 해당하는 기록이 없습니다." />
      )}
      {!resource.loading && !resource.error && items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500">
                {columns.map((column) => (
                  <th key={column} className="whitespace-nowrap px-2 py-2 font-medium">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">{children(items)}</tbody>
          </table>
        </div>
      )}
      <Pagination page={page} totalPages={resource.data?.total_pages} totalCount={resource.data?.total_count} onChange={setPage} />
    </>
  )
}

function ConversationLogs({ enabled }) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ user_name: '', session_id: '', role: '', start_date: '', end_date: '' })
  const [userDraft, setUserDraft] = useState('')

  const resource = useApiResource(
    () => chatbotAdmin.conversationLogs({ page, count: PAGE_SIZE, ...filters }),
    [page, filters.user_name, filters.session_id, filters.role, filters.start_date, filters.end_date],
    { enabled }
  )

  const setFilter = (patch) => {
    setPage(1)
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  return (
    <Card
      title="대화 이력 로그"
      description="사용자·챗봇 메시지 전체를 시간순으로 조회합니다."
      actions={
        <Button variant="secondary" size="sm" onClick={resource.reload}>
          새로고침
        </Button>
      }
      bodyClassName="pt-3"
    >
      <FilterBar>
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">사용자명</span>
          <span className="flex gap-1">
            <Input
              value={userDraft}
              onChange={(event) => setUserDraft(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && setFilter({ user_name: userDraft })}
              placeholder="20241234"
              className="w-32 py-1.5 text-xs"
            />
            <Button variant="secondary" size="sm" onClick={() => setFilter({ user_name: userDraft })}>
              적용
            </Button>
          </span>
        </label>
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">역할</span>
          <Select value={filters.role} onChange={(event) => setFilter({ role: event.target.value })} className="w-auto py-1.5 text-xs">
            <option value="">전체</option>
            {Object.entries(CHAT_ROLE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <DateFilters filters={filters} onChange={setFilter} />
      </FilterBar>

      <TableShell
        resource={resource}
        page={page}
        setPage={setPage}
        emptyTitle="대화 기록이 없습니다"
        columns={['일시', '사용자', '역할', '내용', '의도', '모델', '응답시간', '응답']}
      >
        {(items) =>
          items.map((item) => (
            <tr key={item.id} className="align-top hover:bg-gray-50">
              <td className="whitespace-nowrap px-2 py-2.5 text-gray-500">{formatDateTime(item.created_at)}</td>
              <td className="whitespace-nowrap px-2 py-2.5 text-gray-600">{item.user_name ?? '-'}</td>
              <td className="whitespace-nowrap px-2 py-2.5 text-gray-600">{CHAT_ROLE_LABEL[item.role] ?? item.role}</td>
              <td className="px-2 py-2.5 text-gray-900">
                <p className="line-clamp-3 max-w-md">{item.content}</p>
                <p className="mt-0.5 text-[10px] text-gray-400">세션 {shortId(item.session_id)}</p>
              </td>
              <td className="whitespace-nowrap px-2 py-2.5 text-gray-600">
                {item.detected_intent ? CHAT_INTENT_LABEL[item.detected_intent] ?? item.detected_intent : '-'}
              </td>
              <td className="whitespace-nowrap px-2 py-2.5 text-gray-600">{item.model_name ?? '-'}</td>
              <td className="whitespace-nowrap px-2 py-2.5 tabular-nums text-gray-600">
                {item.latency_ms ? formatMillis(item.latency_ms) : '-'}
              </td>
              <td className="whitespace-nowrap px-2 py-2.5">
                {item.role === 'assistant' ? (
                  item.is_answered ? (
                    <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700">응답</Badge>
                  ) : (
                    <Badge className="border-amber-200 bg-amber-50 text-amber-700">미응답</Badge>
                  )
                ) : (
                  <span className="text-gray-300">-</span>
                )}
              </td>
            </tr>
          ))
        }
      </TableShell>
    </Card>
  )
}

function RetrievalLogs({ enabled }) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ session_id: '', intent: '', start_date: '', end_date: '' })

  const resource = useApiResource(
    () => chatbotAdmin.retrievalLogs({ page, count: PAGE_SIZE, ...filters }),
    [page, filters.session_id, filters.intent, filters.start_date, filters.end_date],
    { enabled }
  )

  const setFilter = (patch) => {
    setPage(1)
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  return (
    <Card
      title="질의응답 검색 로그"
      description="챗봇이 지식베이스에서 무엇을 검색했는지 기록합니다."
      actions={
        <Button variant="secondary" size="sm" onClick={resource.reload}>
          새로고침
        </Button>
      }
      bodyClassName="pt-3"
    >
      <FilterBar>
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">감지 의도</span>
          <Select value={filters.intent} onChange={(event) => setFilter({ intent: event.target.value })} className="w-auto py-1.5 text-xs">
            <option value="">전체</option>
            {Object.entries(CHAT_INTENT_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <DateFilters filters={filters} onChange={setFilter} />
      </FilterBar>

      <TableShell
        resource={resource}
        page={page}
        setPage={setPage}
        emptyTitle="검색 로그가 없습니다"
        columns={['일시', '질의', '의도', '컬렉션', '결과 수', '검색시간']}
      >
        {(items) =>
          items.map((item) => (
            <tr key={item.id} className="align-top hover:bg-gray-50">
              <td className="whitespace-nowrap px-2 py-2.5 text-gray-500">{formatDateTime(item.created_at)}</td>
              <td className="px-2 py-2.5 text-gray-900">
                <p className="line-clamp-2 max-w-md">{item.query_text}</p>
                <p className="mt-0.5 text-[10px] text-gray-400">
                  세션 {shortId(item.session_id)}
                  {item.selected_source_id && ` · 선택 근거 ${shortId(item.selected_source_id)}`}
                </p>
              </td>
              <td className="whitespace-nowrap px-2 py-2.5 text-gray-600">
                {item.detected_intent ? CHAT_INTENT_LABEL[item.detected_intent] ?? item.detected_intent : '-'}
              </td>
              <td className="whitespace-nowrap px-2 py-2.5 font-mono text-[11px] text-gray-600">{item.collection_name ?? '-'}</td>
              <td className="whitespace-nowrap px-2 py-2.5 tabular-nums text-gray-600">{formatNumber(item.result_count)}</td>
              <td className="whitespace-nowrap px-2 py-2.5 tabular-nums text-gray-600">{formatMillis(item.latency_ms)}</td>
            </tr>
          ))
        }
      </TableShell>
    </Card>
  )
}

function UserLogs({ enabled }) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ user_name: '', start_date: daysAgo(29), end_date: today() })
  const [userDraft, setUserDraft] = useState('')

  const resource = useApiResource(
    () => chatbotAdmin.userLogs({ page, count: PAGE_SIZE, ...filters }),
    [page, filters.user_name, filters.start_date, filters.end_date],
    { enabled }
  )

  const setFilter = (patch) => {
    setPage(1)
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  return (
    <Card
      title="사용자별 이용 로그"
      description="기간 내 사용자별 이용량과 만족도를 집계합니다."
      actions={
        <Button variant="secondary" size="sm" onClick={resource.reload}>
          새로고침
        </Button>
      }
      bodyClassName="pt-3"
    >
      <FilterBar>
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">사용자명</span>
          <span className="flex gap-1">
            <Input
              value={userDraft}
              onChange={(event) => setUserDraft(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && setFilter({ user_name: userDraft })}
              placeholder="20241234"
              className="w-32 py-1.5 text-xs"
            />
            <Button variant="secondary" size="sm" onClick={() => setFilter({ user_name: userDraft })}>
              적용
            </Button>
          </span>
        </label>
        <DateFilters filters={filters} onChange={setFilter} />
      </FilterBar>

      <TableShell
        resource={resource}
        page={page}
        setPage={setPage}
        emptyTitle="이용 기록이 없습니다"
        columns={['사용자', '세션 수', '질문 수', '평가 수', '평균 만족도', '최근 활동']}
      >
        {(items) =>
          items.map((item) => (
            <tr key={item.user_name} className="hover:bg-gray-50">
              <td className="whitespace-nowrap px-2 py-2.5 font-medium text-gray-900">{item.user_name}</td>
              <td className="px-2 py-2.5 tabular-nums text-gray-600">{formatNumber(item.session_count)}</td>
              <td className="px-2 py-2.5 tabular-nums text-gray-600">{formatNumber(item.question_count)}</td>
              <td className="px-2 py-2.5 tabular-nums text-gray-600">{formatNumber(item.feedback_count)}</td>
              <td className="px-2 py-2.5 tabular-nums text-gray-600">
                {item.average_rating != null ? formatDecimal(item.average_rating, 2) : '-'}
              </td>
              <td className="whitespace-nowrap px-2 py-2.5 text-gray-500">{formatDateTime(item.last_active_at)}</td>
            </tr>
          ))
        }
      </TableShell>
    </Card>
  )
}

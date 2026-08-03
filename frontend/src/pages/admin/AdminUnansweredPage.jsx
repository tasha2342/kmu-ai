import { useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  InlineNotice,
  Input,
  Pagination,
  Select,
  SkeletonRows,
} from '../../components/ui.jsx'
import { chatbotAdmin } from '../../lib/endpoints.js'
import { useApiResource } from '../../lib/useApiResource.js'
import { useAuth } from '../../lib/useAuth.js'
import { describeError } from '../../lib/api.js'
import { REVIEW_STATUS_LABEL, REVIEW_STATUS_STYLE, UNANSWERED_REASON_LABEL } from '../../lib/constants.js'
import { formatDateTime, shortId } from '../../lib/format.js'

const PAGE_SIZE = 20

/** 미응답 질문 목록과 검토 상태 변경 화면입니다. (KAI-REQ-040) */
export default function AdminUnansweredPage() {
  const session = useAuth()
  const enabled = !!session

  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ reason: '', review_status: '', start_date: '', end_date: '' })
  const [savingId, setSavingId] = useState(null)
  const [message, setMessage] = useState(null)

  const resource = useApiResource(
    () => chatbotAdmin.listUnanswered({ page, count: PAGE_SIZE, ...filters }),
    [page, filters.reason, filters.review_status, filters.start_date, filters.end_date],
    { enabled }
  )

  const setFilter = (patch) => {
    setPage(1)
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  const changeStatus = async (item, reviewStatus) => {
    setSavingId(item.id)
    setMessage(null)
    try {
      await chatbotAdmin.updateUnanswered(item.id, { review_status: reviewStatus })
      setMessage({ tone: 'success', text: '검토 상태를 변경했습니다.' })
      resource.reload()
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    } finally {
      setSavingId(null)
    }
  }

  const items = resource.data?.items ?? []

  return (
    <div className="space-y-4">
      <Card
        title="미응답 질문"
        description="답변하지 못한 질문을 검토하고 FAQ 등록 등 후속 조치를 관리합니다."
        actions={
          <Button variant="secondary" size="sm" onClick={resource.reload}>
            새로고침
          </Button>
        }
        bodyClassName="pt-3"
      >
        <div className="mb-3 flex flex-wrap items-end gap-2">
          <label className="text-xs text-gray-500">
            <span className="mb-1 block">미응답 사유</span>
            <Select
              value={filters.reason}
              onChange={(event) => setFilter({ reason: event.target.value })}
              className="w-auto py-1.5 text-xs"
            >
              <option value="">전체</option>
              {Object.entries(UNANSWERED_REASON_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </label>
          <label className="text-xs text-gray-500">
            <span className="mb-1 block">검토 상태</span>
            <Select
              value={filters.review_status}
              onChange={(event) => setFilter({ review_status: event.target.value })}
              className="w-auto py-1.5 text-xs"
            >
              <option value="">전체</option>
              {Object.entries(REVIEW_STATUS_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </label>
          <label className="text-xs text-gray-500">
            <span className="mb-1 block">시작일</span>
            <Input
              type="date"
              value={filters.start_date}
              onChange={(event) => setFilter({ start_date: event.target.value })}
              className="w-auto py-1.5 text-xs"
            />
          </label>
          <label className="text-xs text-gray-500">
            <span className="mb-1 block">종료일</span>
            <Input
              type="date"
              value={filters.end_date}
              onChange={(event) => setFilter({ end_date: event.target.value })}
              className="w-auto py-1.5 text-xs"
            />
          </label>
        </div>

        {message && (
          <InlineNotice tone={message.tone} className="mb-3">
            {message.text}
          </InlineNotice>
        )}

        {resource.loading && <SkeletonRows rows={6} />}
        {!resource.loading && resource.error && <ErrorNotice error={resource.error} onRetry={resource.reload} />}
        {!resource.loading && !resource.error && items.length === 0 && (
          <EmptyState title="미응답 질문이 없습니다" description="선택한 조건에 해당하는 항목이 없습니다." />
        )}

        {!resource.loading && !resource.error && items.length > 0 && (
          <ul className="divide-y divide-gray-100">
            {items.map((item) => (
              <li key={item.id} className="py-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-gray-900">{item.question_text}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-400">
                      <Badge className="border-gray-200 bg-gray-100 text-gray-600">
                        {UNANSWERED_REASON_LABEL[item.reason] ?? item.reason}
                      </Badge>
                      <Badge className={REVIEW_STATUS_STYLE[item.review_status]}>
                        {REVIEW_STATUS_LABEL[item.review_status] ?? item.review_status}
                      </Badge>
                      <span>세션 {shortId(item.session_id)}</span>
                      <span>· {formatDateTime(item.created_at)}</span>
                      {item.reviewed_by && <span>· 검토자 {item.reviewed_by}</span>}
                      {item.reviewed_at && <span>· {formatDateTime(item.reviewed_at)}</span>}
                    </div>
                  </div>

                  <Select
                    value={item.review_status}
                    disabled={savingId === item.id}
                    onChange={(event) => changeStatus(item, event.target.value)}
                    className="w-auto shrink-0 py-1.5 text-xs"
                    aria-label="검토 상태 변경"
                  >
                    {Object.entries(REVIEW_STATUS_LABEL).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </div>
              </li>
            ))}
          </ul>
        )}

        <Pagination
          page={page}
          totalPages={resource.data?.total_pages}
          totalCount={resource.data?.total_count}
          onChange={setPage}
        />
      </Card>
    </div>
  )
}

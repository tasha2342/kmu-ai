import { useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  InlineNotice,
  Modal,
  Pagination,
  Select,
  SkeletonRows,
} from '../../components/ui.jsx'
import { ingestion } from '../../lib/endpoints.js'
import { useApiResource } from '../../lib/useApiResource.js'
import { useAuth } from '../../lib/useAuth.js'
import { describeError } from '../../lib/api.js'
import {
  INGESTION_ITEM_STATUS_LABEL,
  INGESTION_STATUS_LABEL,
  INGESTION_STATUS_STYLE,
  SOURCE_TYPE_LABEL,
} from '../../lib/constants.js'
import { formatDateTime, formatNumber, shortId } from '../../lib/format.js'

const PAGE_SIZE = 20

/** 지식베이스 수집(재색인) 작업 화면입니다. (KAI-REQ-014) */
export default function AdminIngestionPage() {
  const session = useAuth()
  const enabled = !!session

  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ source_type: '', status: '' })
  const [runOptions, setRunOptions] = useState({ source_type: 'faq', force: false, only_stale: true })
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState(null)
  const [detailId, setDetailId] = useState(null)

  const resource = useApiResource(
    () => ingestion.listJobs({ page, count: PAGE_SIZE, ...filters }),
    [page, filters.source_type, filters.status],
    { enabled }
  )

  const setFilter = (patch) => {
    setPage(1)
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  const run = async () => {
    setRunning(true)
    setMessage(null)
    try {
      const result = await ingestion.runJob(runOptions)
      setMessage({ tone: 'success', text: `${result.message} (작업 ID ${shortId(result.id)})` })
      resource.reload()
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    } finally {
      setRunning(false)
    }
  }

  const remove = async (job) => {
    if (!window.confirm('이 수집 작업 기록을 삭제할까요?')) return
    try {
      await ingestion.deleteJob(job.id)
      setMessage({ tone: 'success', text: '작업 기록을 삭제했습니다.' })
      resource.reload()
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    }
  }

  const items = resource.data?.items ?? []

  return (
    <div className="space-y-4">
      <Card title="재색인 실행" description="FAQ 원문을 임베딩해 벡터 지식베이스에 반영합니다." bodyClassName="pt-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-gray-500">
            <span className="mb-1 block">원천 유형</span>
            <Select
              value={runOptions.source_type}
              onChange={(event) => setRunOptions((prev) => ({ ...prev, source_type: event.target.value }))}
              className="w-auto py-1.5 text-xs"
            >
              {Object.entries(SOURCE_TYPE_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </label>
          <label className="flex items-center gap-2 pb-1.5 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={runOptions.only_stale}
              onChange={(event) => setRunOptions((prev) => ({ ...prev, only_stale: event.target.checked }))}
              className="h-4 w-4 rounded border-gray-300 text-kmu-800 focus:ring-kmu-600"
            />
            재색인이 필요한 항목만
          </label>
          <label className="flex items-center gap-2 pb-1.5 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={runOptions.force}
              onChange={(event) => setRunOptions((prev) => ({ ...prev, force: event.target.checked }))}
              className="h-4 w-4 rounded border-gray-300 text-kmu-800 focus:ring-kmu-600"
            />
            변경이 없어도 강제 재색인
          </label>
          <Button onClick={run} disabled={!enabled || running}>
            {running ? '실행 중…' : '재색인 실행'}
          </Button>
        </div>
        <p className="mt-2 text-[11px] text-gray-400">
          현재 <code className="font-mono">faq</code>만 지원합니다. 공지사항·학칙·문서는 학내 원천 데이터 연계가 제공되지
          않아 요청 시 400을 반환합니다.
        </p>
        {message && (
          <InlineNotice tone={message.tone} className="mt-3">
            {message.text}
          </InlineNotice>
        )}
      </Card>

      <Card
        title="수집 작업 이력"
        actions={
          <Button variant="secondary" size="sm" onClick={resource.reload}>
            새로고침
          </Button>
        }
        bodyClassName="pt-3"
      >
        <div className="mb-3 flex flex-wrap items-end gap-2">
          <label className="text-xs text-gray-500">
            <span className="mb-1 block">원천 유형</span>
            <Select
              value={filters.source_type}
              onChange={(event) => setFilter({ source_type: event.target.value })}
              className="w-auto py-1.5 text-xs"
            >
              <option value="">전체</option>
              {Object.entries(SOURCE_TYPE_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </label>
          <label className="text-xs text-gray-500">
            <span className="mb-1 block">상태</span>
            <Select
              value={filters.status}
              onChange={(event) => setFilter({ status: event.target.value })}
              className="w-auto py-1.5 text-xs"
            >
              <option value="">전체</option>
              {Object.entries(INGESTION_STATUS_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </label>
        </div>

        {resource.loading && <SkeletonRows rows={5} />}
        {!resource.loading && resource.error && <ErrorNotice error={resource.error} onRetry={resource.reload} />}
        {!resource.loading && !resource.error && items.length === 0 && (
          <EmptyState title="수집 작업 기록이 없습니다" description="위에서 재색인을 실행하면 기록이 남습니다." />
        )}

        {!resource.loading && !resource.error && items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-xs">
              <thead>
                <tr className="border-b border-gray-200 text-gray-500">
                  <th className="px-2 py-2 font-medium">작업 ID</th>
                  <th className="px-2 py-2 font-medium">원천</th>
                  <th className="px-2 py-2 font-medium">상태</th>
                  <th className="px-2 py-2 font-medium">전체</th>
                  <th className="px-2 py-2 font-medium">성공</th>
                  <th className="px-2 py-2 font-medium">실패</th>
                  <th className="px-2 py-2 font-medium">시작</th>
                  <th className="px-2 py-2 font-medium">종료</th>
                  <th className="px-2 py-2 font-medium">관리</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {items.map((job) => (
                  <tr key={job.id} className="align-top hover:bg-gray-50">
                    <td className="whitespace-nowrap px-2 py-2.5 font-mono text-[11px] text-gray-600">{shortId(job.id)}</td>
                    <td className="whitespace-nowrap px-2 py-2.5 text-gray-600">
                      {SOURCE_TYPE_LABEL[job.source_type] ?? job.source_type}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2.5">
                      <Badge className={INGESTION_STATUS_STYLE[job.status]}>
                        {INGESTION_STATUS_LABEL[job.status] ?? job.status}
                      </Badge>
                      {job.error_message && <p className="mt-1 max-w-[16rem] text-[10px] text-rose-600">{job.error_message}</p>}
                    </td>
                    <td className="px-2 py-2.5 tabular-nums text-gray-600">{formatNumber(job.total_count)}</td>
                    <td className="px-2 py-2.5 tabular-nums text-gray-600">{formatNumber(job.success_count)}</td>
                    <td className="px-2 py-2.5 tabular-nums text-gray-600">{formatNumber(job.failed_count)}</td>
                    <td className="whitespace-nowrap px-2 py-2.5 text-gray-500">{formatDateTime(job.started_at)}</td>
                    <td className="whitespace-nowrap px-2 py-2.5 text-gray-500">
                      {job.ended_at ? formatDateTime(job.ended_at) : '진행 중'}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2.5">
                      <div className="flex gap-1">
                        <Button variant="secondary" size="sm" onClick={() => setDetailId(job.id)}>
                          상세
                        </Button>
                        <Button variant="danger" size="sm" onClick={() => remove(job)}>
                          삭제
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <Pagination
          page={page}
          totalPages={resource.data?.total_pages}
          totalCount={resource.data?.total_count}
          onChange={setPage}
        />
      </Card>

      <JobDetailModal jobId={detailId} onClose={() => setDetailId(null)} />
    </div>
  )
}

function JobDetailModal({ jobId, onClose }) {
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')

  const detail = useApiResource(() => ingestion.getJob(jobId), [jobId], { enabled: !!jobId })
  const items = useApiResource(
    () => ingestion.listJobItems(jobId, { page, count: PAGE_SIZE, status }),
    [jobId, page, status],
    { enabled: !!jobId }
  )

  const summary = detail.data?.item_summary

  return (
    <Modal open={!!jobId} onClose={onClose} wide title="수집 작업 상세" description={jobId ? `작업 ID ${jobId}` : undefined}>
      {detail.loading && <SkeletonRows rows={3} />}
      {detail.error && <ErrorNotice error={detail.error} onRetry={detail.reload} />}

      {summary && (
        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
          {[
            ['전체', summary.total],
            ['성공', summary.success],
            ['실패', summary.failed],
            ['변경 없음', summary.skipped],
            ['대기', summary.pending],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-gray-200 px-3 py-2">
              <p className="text-[11px] text-gray-500">{label}</p>
              <p className="mt-0.5 text-lg font-semibold tabular-nums text-gray-900">{formatNumber(value)}</p>
            </div>
          ))}
        </div>
      )}

      <div className="mb-2 flex items-end gap-2">
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">항목 상태</span>
          <Select
            value={status}
            onChange={(event) => {
              setPage(1)
              setStatus(event.target.value)
            }}
            className="w-auto py-1.5 text-xs"
          >
            <option value="">전체</option>
            {Object.entries(INGESTION_ITEM_STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
      </div>

      {items.loading && <SkeletonRows rows={5} />}
      {items.error && <ErrorNotice error={items.error} onRetry={items.reload} />}
      {!items.loading && !items.error && (items.data?.items ?? []).length === 0 && (
        <EmptyState title="항목 기록이 없습니다" />
      )}
      {!items.loading && !items.error && (items.data?.items ?? []).length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500">
                <th className="px-2 py-2 font-medium">원천 테이블</th>
                <th className="px-2 py-2 font-medium">원천 ID</th>
                <th className="px-2 py-2 font-medium">상태</th>
                <th className="px-2 py-2 font-medium">오류</th>
                <th className="px-2 py-2 font-medium">생성</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.data.items.map((item) => (
                <tr key={item.id}>
                  <td className="whitespace-nowrap px-2 py-2 font-mono text-[11px] text-gray-600">{item.source_table}</td>
                  <td className="whitespace-nowrap px-2 py-2 font-mono text-[11px] text-gray-600">{shortId(item.source_id)}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-gray-600">
                    {INGESTION_ITEM_STATUS_LABEL[item.status] ?? item.status}
                  </td>
                  <td className="px-2 py-2 text-rose-600">{item.error_message ?? '-'}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-gray-500">{formatDateTime(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination page={page} totalPages={items.data?.total_pages} totalCount={items.data?.total_count} onChange={setPage} />
    </Modal>
  )
}

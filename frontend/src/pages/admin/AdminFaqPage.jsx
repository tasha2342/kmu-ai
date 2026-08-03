import { useState } from 'react'
import FaqEditorModal from './FaqEditorModal.jsx'
import FaqCategoryPanel from './FaqCategoryPanel.jsx'
import FaqSearchPanel from './FaqSearchPanel.jsx'
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
import { faq as faqApi } from '../../lib/endpoints.js'
import { useApiResource } from '../../lib/useApiResource.js'
import { useAuth } from '../../lib/useAuth.js'
import { describeError } from '../../lib/api.js'
import {
  FAQ_STATUS_LABEL,
  FAQ_STATUS_STYLE,
  FAQ_VISIBILITY_LABEL,
  LANGUAGE_LABEL,
} from '../../lib/constants.js'
import { formatDateTime, formatNumber } from '../../lib/format.js'

const PAGE_SIZE = 20

const TABS = [
  { key: 'list', label: 'FAQ 목록' },
  { key: 'category', label: '카테고리' },
  { key: 'search', label: '유사도 검색 테스트' },
]

/** FAQ 지식베이스 관리 화면입니다. */
export default function AdminFaqPage() {
  const session = useAuth()
  const enabled = !!session
  const [tab, setTab] = useState('list')

  const categories = useApiResource(() => faqApi.listCategories(), [], { enabled })

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1">
        {TABS.map((item) => (
          <Button
            key={item.key}
            variant={tab === item.key ? 'subtle' : 'ghost'}
            size="sm"
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </Button>
        ))}
      </div>

      {tab === 'list' && <FaqListPanel enabled={enabled} categories={categories} />}
      {tab === 'category' && <FaqCategoryPanel enabled={enabled} categories={categories} />}
      {tab === 'search' && <FaqSearchPanel enabled={enabled} categories={categories} />}
    </div>
  )
}

function FaqListPanel({ enabled, categories }) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ category_id: '', status: '', visibility: '', language: '', keyword: '' })
  const [keywordDraft, setKeywordDraft] = useState('')
  const [editing, setEditing] = useState(null)
  const [message, setMessage] = useState(null)
  const [syncing, setSyncing] = useState(false)

  const resource = useApiResource(
    () => faqApi.list({ page, count: PAGE_SIZE, ...filters }),
    [page, filters.category_id, filters.status, filters.visibility, filters.language, filters.keyword],
    { enabled }
  )

  const setFilter = (patch) => {
    setPage(1)
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  const remove = async (item) => {
    if (!window.confirm(`"${item.question}" FAQ를 삭제할까요?`)) return
    try {
      await faqApi.remove(item.id)
      setMessage({ tone: 'success', text: 'FAQ를 삭제했습니다.' })
      resource.reload()
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    }
  }

  const syncIndex = async () => {
    setSyncing(true)
    setMessage(null)
    try {
      const result = await faqApi.sync({ force: false })
      setMessage({
        tone: result.failed_count > 0 ? 'warning' : 'success',
        text: `색인 동기화 완료 · 대상 ${formatNumber(result.total_count)}건 / 성공 ${formatNumber(
          result.success_count
        )}건 / 건너뜀 ${formatNumber(result.skipped_count)}건 / 실패 ${formatNumber(result.failed_count)}건${
          result.warning ? ` · ${result.warning}` : ''
        }`,
      })
      resource.reload()
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    } finally {
      setSyncing(false)
    }
  }

  const items = resource.data?.items ?? []
  const categoryList = categories.data?.categories ?? []

  return (
    <Card
      title="FAQ 목록"
      description="챗봇 답변의 근거가 되는 FAQ를 관리합니다. 공개(published) 상태만 색인 대상입니다."
      actions={
        <>
          <Button variant="secondary" size="sm" onClick={syncIndex} disabled={syncing}>
            {syncing ? '동기화 중…' : '색인 동기화'}
          </Button>
          <Button size="sm" onClick={() => setEditing({})}>
            + FAQ 추가
          </Button>
        </>
      }
      bodyClassName="pt-3"
    >
      <div className="mb-3 flex flex-wrap items-end gap-2">
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">카테고리</span>
          <Select
            value={filters.category_id}
            onChange={(event) => setFilter({ category_id: event.target.value })}
            className="w-auto py-1.5 text-xs"
          >
            <option value="">전체</option>
            {categoryList.map((category) => (
              <option key={category.id} value={category.id}>
                {category.category_name}
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
            {Object.entries(FAQ_STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">공개 범위</span>
          <Select
            value={filters.visibility}
            onChange={(event) => setFilter({ visibility: event.target.value })}
            className="w-auto py-1.5 text-xs"
          >
            <option value="">전체</option>
            {Object.entries(FAQ_VISIBILITY_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">언어</span>
          <Select
            value={filters.language}
            onChange={(event) => setFilter({ language: event.target.value })}
            className="w-auto py-1.5 text-xs"
          >
            <option value="">전체</option>
            {Object.entries(LANGUAGE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-xs text-gray-500">
          <span className="mb-1 block">검색어</span>
          <span className="flex gap-1">
            <Input
              value={keywordDraft}
              onChange={(event) => setKeywordDraft(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && setFilter({ keyword: keywordDraft })}
              placeholder="질문·답변 본문"
              className="w-44 py-1.5 text-xs"
            />
            <Button variant="secondary" size="sm" onClick={() => setFilter({ keyword: keywordDraft })}>
              검색
            </Button>
          </span>
        </label>
      </div>

      {message && (
        <InlineNotice tone={message.tone} className="mb-3">
          {message.text}
        </InlineNotice>
      )}
      {categories.error && (
        <InlineNotice tone="warning" className="mb-3">
          카테고리 목록을 불러오지 못했습니다. ({describeError(categories.error)})
        </InlineNotice>
      )}

      {resource.loading && <SkeletonRows rows={6} />}
      {!resource.loading && resource.error && <ErrorNotice error={resource.error} onRetry={resource.reload} />}
      {!resource.loading && !resource.error && items.length === 0 && (
        <EmptyState
          title="등록된 FAQ가 없습니다"
          description="FAQ를 추가하면 챗봇이 해당 내용을 근거로 답변합니다."
          action={<Button size="sm" onClick={() => setEditing({})}>+ FAQ 추가</Button>}
        />
      )}

      {!resource.loading && !resource.error && items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500">
                <th className="px-2 py-2 font-medium">질문</th>
                <th className="px-2 py-2 font-medium">카테고리</th>
                <th className="px-2 py-2 font-medium">상태</th>
                <th className="px-2 py-2 font-medium">공개</th>
                <th className="px-2 py-2 font-medium">수정일</th>
                <th className="px-2 py-2 font-medium">관리</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.map((item) => (
                <tr key={item.id} className="align-top hover:bg-gray-50">
                  <td className="px-2 py-2.5">
                    <p className="font-medium text-gray-900">{item.question}</p>
                    <p className="mt-0.5 line-clamp-2 text-gray-500">{item.answer}</p>
                    {item.tags_json?.length > 0 && (
                      <p className="mt-1 flex flex-wrap gap-1">
                        {item.tags_json.map((tag) => (
                          <Badge key={tag}>#{tag}</Badge>
                        ))}
                      </p>
                    )}
                  </td>
                  <td className="px-2 py-2.5 text-gray-600">{item.category_name ?? '-'}</td>
                  <td className="px-2 py-2.5">
                    <Badge className={FAQ_STATUS_STYLE[item.status]}>{FAQ_STATUS_LABEL[item.status] ?? item.status}</Badge>
                  </td>
                  <td className="px-2 py-2.5 text-gray-600">{FAQ_VISIBILITY_LABEL[item.visibility] ?? item.visibility}</td>
                  <td className="whitespace-nowrap px-2 py-2.5 text-gray-500">{formatDateTime(item.updated_at)}</td>
                  <td className="whitespace-nowrap px-2 py-2.5">
                    <div className="flex gap-1">
                      <Button variant="secondary" size="sm" onClick={() => setEditing(item)}>
                        수정
                      </Button>
                      <Button variant="danger" size="sm" onClick={() => remove(item)}>
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

      <FaqEditorModal
        open={!!editing}
        faq={editing}
        categories={categoryList}
        onClose={() => setEditing(null)}
        onSaved={(text) => {
          setEditing(null)
          setMessage({ tone: 'success', text })
          resource.reload()
        }}
      />
    </Card>
  )
}

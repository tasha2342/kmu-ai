import { useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  InlineNotice,
  Input,
  Modal,
  Pagination,
  Select,
  SkeletonRows,
} from '../../components/ui.jsx'
import { rag } from '../../lib/endpoints.js'
import { useApiResource } from '../../lib/useApiResource.js'
import { useAuth } from '../../lib/useAuth.js'
import { describeError } from '../../lib/api.js'
import { RAG_EMBEDDING_STATUS_LABEL, RAG_EMBEDDING_STATUS_STYLE } from '../../lib/constants.js'
import { formatDateTime, formatNumber } from '../../lib/format.js'

const PAGE_SIZES = [10, 25, 50]

function toMonthFilter(value) {
  if (!value) return ''
  // input[type=month] → YYYY-MM → API YYYY.MM
  return value.replace('-', '.')
}

function currentMonthInput() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

/** RAG Management 화면 — 지식베이스 목록 · 상세 패널 · 동기화/삭제 */
export default function AdminRagPage() {
  const session = useAuth()
  const enabled = !!session

  const [page, setPage] = useState(1)
  const [count, setCount] = useState(25)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState({ status: '', is_active: '', date: '' })
  const [selected, setSelected] = useState(() => new Set())
  const [detailName, setDetailName] = useState(null)
  const [detailTick, setDetailTick] = useState(0)
  const [logsOpen, setLogsOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    const timer = setTimeout(() => {
      setPage(1)
      setSearch(searchInput.trim())
    }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  const listParams = useMemo(
    () => ({
      page,
      count,
      search: search || undefined,
      status: filters.status || undefined,
      is_active: filters.is_active === '' ? undefined : filters.is_active === 'true',
      date: toMonthFilter(filters.date) || undefined,
      include_system: true,
    }),
    [page, count, search, filters]
  )

  const resource = useApiResource(() => rag.listItems(listParams), [listParams], { enabled })
  const items = resource.data?.items ?? []

  const setFilter = (patch) => {
    setPage(1)
    setSelected(new Set())
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  const allNames = items.map((item) => item.name)
  const allSelected = allNames.length > 0 && allNames.every((name) => selected.has(name))

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set())
      return
    }
    setSelected(new Set(allNames))
  }

  const toggleOne = (name) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const runAction = async (fn, successText) => {
    setBusy(true)
    setMessage(null)
    try {
      const result = await fn()
      setMessage({ tone: 'success', text: successText ?? result?.message ?? '완료했습니다.' })
      resource.reload()
      setDetailTick((tick) => tick + 1)
      return result
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
      return null
    } finally {
      setBusy(false)
    }
  }

  const syncNames = async (names) => {
    if (!names.length) return
    const result = await runAction(
      () => rag.sync({ names, force: false, only_stale: true }),
      null
    )
    if (result?.message) setMessage({ tone: 'success', text: result.message })
  }

  const deleteNames = async (names) => {
    if (!names.length) return
    if (!window.confirm(`${names.length}개 지식베이스를 삭제할까요?\n시스템 KB는 보호되어 삭제되지 않습니다.`)) return
    const result = await runAction(() => rag.remove(names), null)
    if (result?.message) setMessage({ tone: 'success', text: result.message })
    setSelected(new Set())
    if (names.includes(detailName)) setDetailName(null)
  }

  const toggleActive = async (item, next) => {
    await runAction(() => rag.setActive(item.name, next), `${item.display_name} 활성 상태를 변경했습니다.`)
  }

  const exportList = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const data = await rag.exportItems({
        search: search || undefined,
        status: filters.status || undefined,
        is_active: filters.is_active === '' ? undefined : filters.is_active === 'true',
      })
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `rag-items-${Date.now()}.json`
      anchor.click()
      URL.revokeObjectURL(url)
      setMessage({ tone: 'success', text: `${data.total_count ?? 0}건을 내보냈습니다.` })
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    } finally {
      setBusy(false)
    }
  }

  const syncManaged = () => syncNames(['kmu_faq_knowledge', 'kmu_regulations'])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-base font-semibold text-gray-900">RAG Management</h1>
          <p className="text-xs text-gray-500">지식베이스 동기화 · 활성 · 문서/청크 현황을 관리합니다.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => setUploadOpen(true)} disabled={!enabled || busy} variant="secondary">
            파일 업로드
          </Button>
          <Button onClick={syncManaged} disabled={!enabled || busy}>
            동기화
          </Button>
        </div>
      </div>

      {message && (
        <InlineNotice tone={message.tone}>
          {message.text}
        </InlineNotice>
      )}

      <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
        <div className={`min-w-0 flex-1 space-y-2 ${detailName ? 'lg:max-w-[calc(100%-20rem)]' : ''}`}>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2">
            <Input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="검색"
              className="!w-48 shrink-0 py-1.5 text-xs"
              disabled={!enabled}
            />
            <Select
              value={filters.is_active}
              onChange={(event) => setFilter({ is_active: event.target.value })}
              className="!w-28 shrink-0 py-1.5 text-xs"
              disabled={!enabled}
            >
              <option value="">전체</option>
              <option value="true">활성</option>
              <option value="false">비활성</option>
            </Select>
            <Select
              value={filters.status}
              onChange={(event) => setFilter({ status: event.target.value })}
              className="!w-32 shrink-0 py-1.5 text-xs"
              disabled={!enabled}
            >
              <option value="">상태</option>
              {Object.entries(RAG_EMBEDDING_STATUS_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
            <Input
              type="month"
              value={filters.date}
              onChange={(event) => setFilter({ date: event.target.value })}
              className="!w-40 shrink-0 py-1.5 text-xs"
              disabled={!enabled}
              title="월 필터 (updated_at)"
            />
            {filters.date ? (
              <Button variant="ghost" size="sm" className="shrink-0" onClick={() => setFilter({ date: '' })}>
                날짜 해제
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0"
                onClick={() => setFilter({ date: currentMonthInput() })}
                title="이번 달로 필터"
              >
                이번 달
              </Button>
            )}
          </div>

          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
            <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 px-3 py-2">
              <Button
                variant="danger"
                size="sm"
                disabled={!enabled || busy || selected.size === 0}
                onClick={() => deleteNames([...selected])}
              >
                일괄 삭제
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={!enabled || busy || selected.size === 0}
                onClick={() => syncNames([...selected])}
              >
                일괄 동기화
              </Button>
              <Button variant="secondary" size="sm" disabled={!enabled || busy} onClick={exportList}>
                내보내기
              </Button>
              <Button variant="ghost" size="sm" onClick={resource.reload} disabled={!enabled}>
                새로고침
              </Button>
              {selected.size > 0 && (
                <span className="text-[11px] text-gray-500">{selected.size}개 선택</span>
              )}
            </div>

            {resource.loading && (
              <div className="p-3">
                <SkeletonRows rows={6} />
              </div>
            )}
            {!resource.loading && resource.error && (
              <div className="p-3">
                <ErrorNotice error={resource.error} onRetry={resource.reload} />
              </div>
            )}
            {!resource.loading && !resource.error && items.length === 0 && (
              <div className="p-3">
                <EmptyState
                  title="지식베이스가 없습니다"
                  description="컬렉션을 만들거나 FAQ/규정 재색인을 실행하면 목록에 나타납니다."
                />
              </div>
            )}

            {!resource.loading && !resource.error && items.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] text-left text-xs">
                  <thead>
                    <tr className="border-b border-gray-200 bg-gray-50 text-[11px] text-gray-500">
                      <th className="w-8 px-2 py-2">
                        <input
                          type="checkbox"
                          checked={allSelected}
                          onChange={toggleAll}
                          className="h-3.5 w-3.5 rounded border-gray-300 text-kmu-800 focus:ring-kmu-600"
                        />
                      </th>
                      <th className="px-2 py-2 font-medium">지식 베이스</th>
                      <th className="px-2 py-2 font-medium">봇</th>
                      <th className="px-2 py-2 font-medium">문서</th>
                      <th className="px-2 py-2 font-medium">청크</th>
                      <th className="px-2 py-2 font-medium">임베딩 상태</th>
                      <th className="px-2 py-2 font-medium">최근 동기화</th>
                      <th className="px-2 py-2 font-medium">활성</th>
                      <th className="px-2 py-2 font-medium">작업</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {items.map((item) => {
                      const activeRow = detailName === item.name
                      return (
                        <tr
                          key={item.name}
                          className={`cursor-pointer align-middle hover:bg-gray-50 ${
                            activeRow ? 'bg-kmu-50/60' : ''
                          }`}
                          onClick={() => setDetailName(item.name)}
                        >
                          <td className="px-2 py-2" onClick={(event) => event.stopPropagation()}>
                            <input
                              type="checkbox"
                              checked={selected.has(item.name)}
                              onChange={() => toggleOne(item.name)}
                              className="h-3.5 w-3.5 rounded border-gray-300 text-kmu-800 focus:ring-kmu-600"
                            />
                          </td>
                          <td className="px-2 py-2">
                            <div className="font-medium text-gray-900">{item.display_name}</div>
                            <div className="text-[10px] text-gray-400">
                              {item.display_name_en || item.name}
                            </div>
                          </td>
                          <td className="whitespace-nowrap px-2 py-2 text-gray-600">
                            <div>{item.bot_label || '-'}</div>
                            {item.bot_label_en && (
                              <div className="text-[10px] text-gray-400">{item.bot_label_en}</div>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-2 py-2 tabular-nums text-gray-700">
                            {formatDocCount(item.document_count)}
                          </td>
                          <td className="whitespace-nowrap px-2 py-2 tabular-nums text-gray-700">
                            {formatNumber(item.chunk_count)}
                          </td>
                          <td className="whitespace-nowrap px-2 py-2">
                            <Badge className={RAG_EMBEDDING_STATUS_STYLE[item.embedding_status]}>
                              {item.embedding_status_label ||
                                RAG_EMBEDDING_STATUS_LABEL[item.embedding_status] ||
                                item.embedding_status}
                            </Badge>
                          </td>
                          <td className="whitespace-nowrap px-2 py-2 text-gray-500">
                            {formatDateTime(item.last_synced_at)}
                          </td>
                          <td className="px-2 py-2" onClick={(event) => event.stopPropagation()}>
                            <button
                              type="button"
                              role="switch"
                              aria-checked={item.is_active}
                              disabled={busy}
                              onClick={() => toggleActive(item, !item.is_active)}
                              className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
                                item.is_active ? 'bg-sky-500' : 'bg-gray-300'
                              }`}
                            >
                              <span
                                className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
                                  item.is_active ? 'left-4' : 'left-0.5'
                                }`}
                              />
                            </button>
                          </td>
                          <td className="px-2 py-2" onClick={(event) => event.stopPropagation()}>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-rose-500 hover:bg-rose-50 hover:text-rose-700"
                              disabled={busy}
                              onClick={() => deleteNames([item.name])}
                              aria-label="삭제"
                            >
                              🗑
                            </Button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 px-3 py-2">
              <Pagination
                page={page}
                totalPages={resource.data?.total_pages}
                totalCount={resource.data?.total_count}
                onChange={setPage}
                className="pt-0"
              />
              <label className="flex items-center gap-1.5 text-[11px] text-gray-500">
                페이지당
                <Select
                  value={count}
                  onChange={(event) => {
                    setPage(1)
                    setCount(Number(event.target.value))
                  }}
                  className="w-auto py-1 text-xs"
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </Select>
              </label>
            </div>
          </div>
        </div>

        {detailName && (
          <DetailPanel
            name={detailName}
            enabled={enabled}
            busy={busy}
            onClose={() => {
              setDetailName(null)
              setLogsOpen(false)
            }}
            onSync={() => syncNames([detailName])}
            onOpenLogs={() => setLogsOpen(true)}
            onSaved={() => {
              resource.reload()
              setDetailTick((tick) => tick + 1)
            }}
            onMessage={setMessage}
            reloadToken={detailTick}
          />
        )}
      </div>

      <LogsModal name={logsOpen ? detailName : null} onClose={() => setLogsOpen(false)} />
      <UploadModal
        open={uploadOpen}
        collections={items}
        defaultName={detailName}
        enabled={enabled}
        onClose={() => setUploadOpen(false)}
        onDone={(text) => {
          setMessage({ tone: 'success', text })
          resource.reload()
          setDetailTick((tick) => tick + 1)
          setUploadOpen(false)
        }}
        onError={(text) => setMessage({ tone: 'error', text })}
      />
    </div>
  )
}

function formatDocCount(value) {
  const num = Number(value)
  if (Number.isNaN(num)) return '-'
  if (num >= 1000) return `${Math.floor(num / 100) * 100}+`
  if (num >= 100) return `${Math.floor(num / 10) * 10}+`
  return formatNumber(num)
}

function DetailPanel({ name, enabled, busy, onClose, onSync, onOpenLogs, onSaved, onMessage, reloadToken }) {
  const detail = useApiResource(() => rag.getItem(name), [name, reloadToken], { enabled: enabled && !!name })
  const item = detail.data
  const [settings, setSettings] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!item) return
    setSettings({
      chunk_size: item.chunk_size ?? 1000,
      chunk_overlap: item.chunk_overlap ?? 100,
      top_k: item.top_k ?? 5,
      similarity_threshold: item.similarity_threshold ?? 0.35,
    })
  }, [item])

  const saveSettings = async () => {
    if (!settings) return
    setSaving(true)
    try {
      await rag.updateItem(name, {
        chunk_size: Number(settings.chunk_size),
        chunk_overlap: Number(settings.chunk_overlap),
        top_k: Number(settings.top_k),
        similarity_threshold: Number(settings.similarity_threshold),
      })
      onMessage?.({ tone: 'success', text: '검색/청킹 설정을 저장했습니다.' })
      onSaved?.()
      detail.reload()
    } catch (error) {
      onMessage?.({ tone: 'error', text: describeError(error) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <aside className="w-full shrink-0 rounded-lg border border-gray-200 bg-white lg:sticky lg:top-3 lg:w-80">
      <header className="flex items-start justify-between gap-2 border-b border-gray-100 px-3 py-2.5">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">상세 정보</h2>
          {item && (
            <p className="mt-0.5 text-[11px] text-gray-500">
              선택 항목: <span className="font-medium text-gray-700">{item.display_name}</span>
            </p>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="닫기">
          ✕
        </Button>
      </header>

      <div className="space-y-3 px-3 py-3 text-xs">
        {detail.loading && <SkeletonRows rows={4} />}
        {detail.error && <ErrorNotice error={detail.error} onRetry={detail.reload} />}
        {!detail.loading && !detail.error && item && settings && (
          <>
            <dl className="space-y-2">
              <StatRow label="문서 수" value={formatNumber(item.document_count)} />
              <StatRow label="총 청크" value={formatNumber(item.chunk_count)} />
              <StatRow label="벡터 DB" value={item.vector_db || 'pgvector'} />
              <StatRow label="최근 에러" value={item.recent_error || '없음'} danger={!!item.recent_error} />
              <StatRow label="임베딩 모델" value={item.embedding_model} />
              <StatRow
                label="상태"
                value={
                  <Badge className={RAG_EMBEDDING_STATUS_STYLE[item.embedding_status]}>
                    {item.embedding_status_label || item.embedding_status}
                  </Badge>
                }
              />
              <StatRow label="최근 동기화" value={formatDateTime(item.last_synced_at)} />
            </dl>

            <div className="space-y-2 border-t border-gray-100 pt-3">
              <p className="text-[11px] font-semibold text-gray-800">검색 · 청킹 설정</p>
              <label className="block">
                <span className="mb-0.5 block text-[11px] text-gray-500">chunk_size</span>
                <Input
                  type="number"
                  min={100}
                  value={settings.chunk_size}
                  onChange={(event) => setSettings((prev) => ({ ...prev, chunk_size: event.target.value }))}
                  className="!w-full py-1.5 text-xs"
                />
              </label>
              <label className="block">
                <span className="mb-0.5 block text-[11px] text-gray-500">chunk_overlap</span>
                <Input
                  type="number"
                  min={0}
                  value={settings.chunk_overlap}
                  onChange={(event) => setSettings((prev) => ({ ...prev, chunk_overlap: event.target.value }))}
                  className="!w-full py-1.5 text-xs"
                />
              </label>
              <label className="block">
                <span className="mb-0.5 block text-[11px] text-gray-500">top_k</span>
                <Input
                  type="number"
                  min={1}
                  max={50}
                  value={settings.top_k}
                  onChange={(event) => setSettings((prev) => ({ ...prev, top_k: event.target.value }))}
                  className="!w-full py-1.5 text-xs"
                />
              </label>
              <label className="block">
                <span className="mb-0.5 block text-[11px] text-gray-500">similarity_threshold</span>
                <Input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={settings.similarity_threshold}
                  onChange={(event) =>
                    setSettings((prev) => ({ ...prev, similarity_threshold: event.target.value }))
                  }
                  className="!w-full py-1.5 text-xs"
                />
              </label>
              <Button size="sm" className="w-full" onClick={saveSettings} disabled={saving || !enabled}>
                {saving ? '저장 중…' : '설정 저장'}
              </Button>
            </div>

            <div className="flex flex-col gap-2 pt-1">
              <Button onClick={onSync} disabled={busy || !enabled}>
                즉시 동기화
              </Button>
              <Button variant="secondary" onClick={onOpenLogs} disabled={!enabled}>
                로그 보기
              </Button>
            </div>
          </>
        )}
      </div>
    </aside>
  )
}

function UploadModal({ open, collections, defaultName, enabled, onClose, onDone, onError }) {
  const uploadable = (collections || []).filter((item) => item.name !== 'kmu_faq_knowledge')
  const [collectionName, setCollectionName] = useState('')
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)

  useEffect(() => {
    if (!open) return
    const preferred =
      (defaultName && uploadable.some((item) => item.name === defaultName) && defaultName) ||
      uploadable[0]?.name ||
      ''
    setCollectionName(preferred)
    setFile(null)
  }, [open, defaultName, collections])

  const submit = async () => {
    if (!collectionName || !file) {
      onError?.('컬렉션과 파일을 선택해주세요.')
      return
    }
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('background', 'true')
      const selected = uploadable.find((item) => item.name === collectionName)
      if (selected?.chunk_size) formData.append('chunk_size', String(selected.chunk_size))
      if (selected?.chunk_overlap != null) formData.append('chunk_overlap', String(selected.chunk_overlap))
      const result = await rag.upload(collectionName, formData)
      onDone?.(result?.message || `${file.name} 업로드를 시작했습니다.`)
    } catch (error) {
      onError?.(describeError(error))
    } finally {
      setUploading(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="문서 업로드"
      description="선택한 지식베이스에 파일을 업로드하고 임베딩합니다."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={uploading}>
            취소
          </Button>
          <Button onClick={submit} disabled={!enabled || uploading || !collectionName || !file}>
            {uploading ? '업로드 중…' : '업로드'}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-xs">
        <label className="block">
          <span className="mb-1 block font-medium text-gray-600">지식베이스</span>
          <Select
            value={collectionName}
            onChange={(event) => setCollectionName(event.target.value)}
            className="!w-full py-1.5 text-xs"
          >
            {uploadable.length === 0 && <option value="">업로드 가능한 컬렉션 없음</option>}
            {uploadable.map((item) => (
              <option key={item.name} value={item.name}>
                {item.display_name} ({item.name})
              </option>
            ))}
          </Select>
        </label>
        <label className="block">
          <span className="mb-1 block font-medium text-gray-600">파일</span>
          <input
            type="file"
            accept=".pdf,.doc,.docx,.txt,.hwp,.hwpx,.md"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
            className="block w-full text-xs text-gray-600 file:mr-3 file:rounded-lg file:border-0 file:bg-kmu-50 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-kmu-800"
          />
        </label>
        <p className="text-[11px] text-gray-400">
          FAQ 지식베이스는 FAQ 관리에서 등록하세요. 청킹 값은 컬렉션 설정의 chunk_size / chunk_overlap을 사용합니다.
        </p>
      </div>
    </Modal>
  )
}

function StatRow({ label, value, danger = false }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-gray-50 pb-1.5 last:border-0">
      <dt className="shrink-0 text-gray-500">{label}</dt>
      <dd className={`min-w-0 text-right font-medium ${danger ? 'text-rose-600' : 'text-gray-800'}`}>{value}</dd>
    </div>
  )
}

function LogsModal({ name, onClose }) {
  const [page, setPage] = useState(1)
  const logs = useApiResource(
    () => rag.listLogs(name, { page, count: 20 }),
    [name, page],
    { enabled: !!name }
  )

  useEffect(() => {
    setPage(1)
  }, [name])

  const items = logs.data?.items ?? []

  return (
    <Modal
      open={!!name}
      onClose={onClose}
      wide
      title="동기화 로그"
      description={name ? `지식베이스: ${name}` : undefined}
    >
      {logs.loading && <SkeletonRows rows={4} />}
      {logs.error && <ErrorNotice error={logs.error} onRetry={logs.reload} />}
      {!logs.loading && !logs.error && items.length === 0 && (
        <EmptyState title="로그가 없습니다" description="동기화 작업이나 문서 오류가 아직 없습니다." />
      )}
      {!logs.loading && !logs.error && items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500">
                <th className="px-2 py-2 font-medium">유형</th>
                <th className="px-2 py-2 font-medium">상태</th>
                <th className="px-2 py-2 font-medium">메시지</th>
                <th className="px-2 py-2 font-medium">일시</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.map((log) => (
                <tr key={`${log.event_type}-${log.id}`} className="align-top">
                  <td className="whitespace-nowrap px-2 py-2 text-gray-600">{log.event_type}</td>
                  <td className="whitespace-nowrap px-2 py-2">
                    <Badge>{log.status}</Badge>
                  </td>
                  <td className="max-w-md px-2 py-2 text-gray-700">{log.message || '-'}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-gray-500">
                    {formatDateTime(log.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pagination
        page={page}
        totalPages={logs.data?.total_pages}
        totalCount={logs.data?.total_count}
        onChange={setPage}
      />
    </Modal>
  )
}

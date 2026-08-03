import { useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  InlineNotice,
  Input,
  Pagination,
  Select,
  SkeletonRows,
  Textarea,
} from '../../components/ui.jsx'
import { masking } from '../../lib/endpoints.js'
import { useApiResource } from '../../lib/useApiResource.js'
import { useAuth } from '../../lib/useAuth.js'
import { describeError } from '../../lib/api.js'
import {
  MASKING_ACTIVE_STYLE,
  MASKING_METHOD_LABEL,
  MASKING_TARGET_FIELD_LABEL,
} from '../../lib/constants.js'
import { formatDateTime, shortId } from '../../lib/format.js'

const PAGE_SIZES = [10, 25, 50]

const EMPTY_FORM = {
  name: '',
  target_field: 'phone',
  regex_pattern: '',
  masking_method: 'middle',
  replacement: '****',
  description: '',
  is_active: true,
}

/** 개인정보 마스킹 규칙 관리 화면 */
export default function AdminMaskingPage() {
  const session = useAuth()
  const enabled = !!session

  const [page, setPage] = useState(1)
  const [count, setCount] = useState(10)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState({ is_active: '', masking_method: '' })
  const [panelMode, setPanelMode] = useState(null) // null | 'create' | 'edit'
  const [editingId, setEditingId] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const [listTick, setListTick] = useState(0)

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
      is_active: filters.is_active === '' ? undefined : filters.is_active === 'true',
      masking_method: filters.masking_method || undefined,
    }),
    [page, count, search, filters, listTick]
  )

  const resource = useApiResource(() => masking.listItems(listParams), [listParams], { enabled })
  const items = resource.data?.items ?? []

  const setFilter = (patch) => {
    setPage(1)
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  const openCreate = () => {
    setPanelMode('create')
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  const openEdit = (item) => {
    setPanelMode('edit')
    setEditingId(item.id)
    setForm({
      name: item.name || '',
      target_field: item.target_field || 'custom',
      regex_pattern: item.regex_pattern || '',
      masking_method: item.masking_method || 'partial',
      replacement: item.replacement || '****',
      description: item.description || '',
      is_active: !!item.is_active,
    })
  }

  const closePanel = () => {
    setPanelMode(null)
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  const save = async () => {
    if (!form.name.trim() || !form.regex_pattern.trim()) {
      setMessage({ tone: 'error', text: '규칙명과 정규표현식은 필수입니다.' })
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const body = {
        name: form.name.trim(),
        target_field: form.target_field,
        regex_pattern: form.regex_pattern.trim(),
        masking_method: form.masking_method,
        replacement: form.replacement || '****',
        description: form.description || null,
        is_active: form.is_active,
      }
      if (panelMode === 'create') {
        await masking.create(body)
        setMessage({ tone: 'success', text: '규칙을 추가했습니다.' })
      } else {
        await masking.update(editingId, body)
        setMessage({ tone: 'success', text: '규칙을 저장했습니다.' })
      }
      setListTick((tick) => tick + 1)
      resource.reload()
      closePanel()
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    } finally {
      setBusy(false)
    }
  }

  const remove = async (item) => {
    if (!window.confirm(`「${item.name}」 규칙을 삭제할까요?`)) return
    setBusy(true)
    setMessage(null)
    try {
      await masking.remove(item.id)
      setMessage({ tone: 'success', text: '규칙을 삭제했습니다.' })
      if (editingId === item.id) closePanel()
      setListTick((tick) => tick + 1)
      resource.reload()
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-base font-semibold text-gray-900">개인정보 비식별화 (Masking) 관리</h1>
          <p className="text-xs text-gray-500">정규표현식 기반 개인정보 필터링 규칙을 관리합니다.</p>
        </div>
        <Button onClick={openCreate} disabled={!enabled || busy}>
          + 규칙 추가
        </Button>
      </div>

      {message && <InlineNotice tone={message.tone}>{message.text}</InlineNotice>}

      <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
        <div className={`min-w-0 flex-1 space-y-2 ${panelMode ? 'lg:max-w-[calc(100%-22rem)]' : ''}`}>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2">
            <Input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="규칙명 또는 대상 필드 검색"
              className="max-w-xs py-1.5 text-xs"
              disabled={!enabled}
            />
            <Select
              value={filters.is_active}
              onChange={(event) => setFilter({ is_active: event.target.value })}
              className="w-auto py-1.5 text-xs"
              disabled={!enabled}
            >
              <option value="">전체 상태</option>
              <option value="true">활성</option>
              <option value="false">비활성</option>
            </Select>
            <Select
              value={filters.masking_method}
              onChange={(event) => setFilter({ masking_method: event.target.value })}
              className="w-auto py-1.5 text-xs"
              disabled={!enabled}
            >
              <option value="">전체 마스킹 방식</option>
              {Object.entries(MASKING_METHOD_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
            <Button variant="ghost" size="sm" onClick={resource.reload} disabled={!enabled}>
              새로고침
            </Button>
          </div>

          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
            {resource.loading && (
              <div className="p-3">
                <SkeletonRows rows={5} />
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
                  title="마스킹 규칙이 없습니다"
                  description="우측 상단에서 규칙을 추가하거나 시드 마이그레이션을 적용하세요."
                  action={
                    <Button size="sm" onClick={openCreate} disabled={!enabled}>
                      + 규칙 추가
                    </Button>
                  }
                />
              </div>
            )}

            {!resource.loading && !resource.error && items.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px] text-left text-xs">
                  <thead>
                    <tr className="border-b border-gray-200 bg-gray-50 text-[11px] text-gray-500">
                      <th className="px-2 py-2 font-medium">ID</th>
                      <th className="px-2 py-2 font-medium">규칙명</th>
                      <th className="px-2 py-2 font-medium">대상 필드</th>
                      <th className="px-2 py-2 font-medium">정규표현식</th>
                      <th className="px-2 py-2 font-medium">마스킹 방식</th>
                      <th className="px-2 py-2 font-medium">상태</th>
                      <th className="px-2 py-2 font-medium">수정일</th>
                      <th className="px-2 py-2 font-medium">관리</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {items.map((item) => {
                      const active = editingId === item.id
                      return (
                        <tr
                          key={item.id}
                          className={`align-middle hover:bg-gray-50 ${active ? 'bg-kmu-50/60' : ''}`}
                        >
                          <td className="whitespace-nowrap px-2 py-2 font-mono text-[11px] text-gray-500">
                            {shortId(item.id)}
                          </td>
                          <td className="px-2 py-2 font-medium text-gray-900">{item.name}</td>
                          <td className="whitespace-nowrap px-2 py-2 text-gray-600">
                            {MASKING_TARGET_FIELD_LABEL[item.target_field] ?? item.target_field}
                          </td>
                          <td className="max-w-[14rem] truncate px-2 py-2 font-mono text-[11px] text-gray-600" title={item.regex_pattern}>
                            {item.regex_pattern}
                          </td>
                          <td className="whitespace-nowrap px-2 py-2 text-gray-600">
                            {MASKING_METHOD_LABEL[item.masking_method] ?? item.masking_method}
                          </td>
                          <td className="whitespace-nowrap px-2 py-2">
                            <Badge className={MASKING_ACTIVE_STYLE[String(!!item.is_active)]}>
                              {item.is_active ? '활성' : '비활성'}
                            </Badge>
                          </td>
                          <td className="whitespace-nowrap px-2 py-2 text-gray-500">
                            {formatDateTime(item.updated_at)}
                          </td>
                          <td className="whitespace-nowrap px-2 py-2">
                            <div className="flex gap-1">
                              <Button variant="secondary" size="sm" onClick={() => openEdit(item)} disabled={busy}>
                                수정
                              </Button>
                              <Button variant="danger" size="sm" onClick={() => remove(item)} disabled={busy}>
                                삭제
                              </Button>
                            </div>
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

        {panelMode && (
          <aside className="w-full shrink-0 rounded-lg border border-gray-200 bg-white lg:sticky lg:top-3 lg:w-[22rem]">
            <header className="flex items-start justify-between gap-2 border-b border-gray-100 px-3 py-2.5">
              <h2 className="text-sm font-semibold text-gray-900">규칙 상세 정보</h2>
              <Button variant="ghost" size="sm" onClick={closePanel} aria-label="닫기">
                ✕
              </Button>
            </header>
            <div className="space-y-3 px-3 py-3">
              <Field label="규칙명 *">
                <Input
                  value={form.name}
                  onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                  className="py-1.5 text-xs"
                />
              </Field>
              <Field label="대상 필드 *">
                <Select
                  value={form.target_field}
                  onChange={(event) => setForm((prev) => ({ ...prev, target_field: event.target.value }))}
                  className="py-1.5 text-xs"
                >
                  {Object.entries(MASKING_TARGET_FIELD_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="정규표현식 *" hint="잘못된 패턴은 저장 시 거부됩니다.">
                <Input
                  value={form.regex_pattern}
                  onChange={(event) => setForm((prev) => ({ ...prev, regex_pattern: event.target.value }))}
                  className="py-1.5 font-mono text-xs"
                />
              </Field>
              <Field label="마스킹 방식 *">
                <Select
                  value={form.masking_method}
                  onChange={(event) => setForm((prev) => ({ ...prev, masking_method: event.target.value }))}
                  className="py-1.5 text-xs"
                >
                  {Object.entries(MASKING_METHOD_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="치환 문자열">
                <Input
                  value={form.replacement}
                  onChange={(event) => setForm((prev) => ({ ...prev, replacement: event.target.value }))}
                  className="py-1.5 font-mono text-xs"
                />
              </Field>
              <Field label="설명">
                <Textarea
                  rows={3}
                  value={form.description}
                  onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                  className="text-xs"
                />
              </Field>
              <div className="flex items-center justify-between gap-3 border-t border-gray-50 pt-2">
                <span className="text-xs text-gray-600">상태</span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.is_active}
                  onClick={() => setForm((prev) => ({ ...prev, is_active: !prev.is_active }))}
                  className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
                    form.is_active ? 'bg-sky-500' : 'bg-gray-300'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
                      form.is_active ? 'left-4' : 'left-0.5'
                    }`}
                  />
                </button>
                <span className="text-xs font-medium text-gray-700">{form.is_active ? '활성' : '비활성'}</span>
              </div>
              <div className="flex gap-2 pt-1">
                <Button variant="secondary" className="flex-1" onClick={closePanel} disabled={busy}>
                  취소
                </Button>
                <Button className="flex-1" onClick={save} disabled={busy || !enabled}>
                  {busy ? '저장 중…' : '저장'}
                </Button>
              </div>
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}

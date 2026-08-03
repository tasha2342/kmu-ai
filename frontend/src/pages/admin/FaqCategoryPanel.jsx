import { useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Field,
  InlineNotice,
  Input,
  Modal,
  Select,
  SkeletonRows,
} from '../../components/ui.jsx'
import { faq as faqApi } from '../../lib/endpoints.js'
import { describeError } from '../../lib/api.js'
import { formatNumber } from '../../lib/format.js'

const EMPTY = {
  category_name: '',
  category_code: '',
  parent_id: '',
  department_code: '',
  display_order: 0,
  is_active: true,
}

/** FAQ 카테고리 CRUD 패널입니다. */
export default function FaqCategoryPanel({ enabled, categories }) {
  const [editing, setEditing] = useState(null)
  const [message, setMessage] = useState(null)

  const items = categories.data?.categories ?? []

  const remove = async (item) => {
    if (!window.confirm(`"${item.category_name}" 카테고리를 삭제할까요?`)) return
    try {
      await faqApi.deleteCategory(item.id)
      setMessage({ tone: 'success', text: '카테고리를 삭제했습니다.' })
      categories.reload()
    } catch (error) {
      setMessage({ tone: 'error', text: describeError(error) })
    }
  }

  return (
    <Card
      title="FAQ 카테고리"
      description="FAQ를 분류하는 카테고리를 관리합니다. 상위 카테고리를 지정하면 계층 구조가 됩니다."
      actions={
        <>
          <Button variant="secondary" size="sm" onClick={categories.reload}>
            새로고침
          </Button>
          <Button size="sm" onClick={() => setEditing({})}>
            + 카테고리 추가
          </Button>
        </>
      }
      bodyClassName="pt-3"
    >
      {message && (
        <InlineNotice tone={message.tone} className="mb-3">
          {message.text}
        </InlineNotice>
      )}

      {!enabled && <EmptyState title="로그인이 필요합니다" description="로그인하면 목록을 조회합니다." />}
      {enabled && categories.loading && <SkeletonRows rows={4} />}
      {enabled && !categories.loading && categories.error && (
        <ErrorNotice error={categories.error} onRetry={categories.reload} />
      )}
      {enabled && !categories.loading && !categories.error && items.length === 0 && (
        <EmptyState
          title="등록된 카테고리가 없습니다"
          description="FAQ를 등록하려면 카테고리가 최소 1개 필요합니다."
          action={<Button size="sm" onClick={() => setEditing({})}>+ 카테고리 추가</Button>}
        />
      )}

      {enabled && !categories.loading && !categories.error && items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500">
                <th className="px-2 py-2 font-medium">카테고리명</th>
                <th className="px-2 py-2 font-medium">코드</th>
                <th className="px-2 py-2 font-medium">담당 부서</th>
                <th className="px-2 py-2 font-medium">순서</th>
                <th className="px-2 py-2 font-medium">FAQ 수</th>
                <th className="px-2 py-2 font-medium">사용</th>
                <th className="px-2 py-2 font-medium">관리</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-2 py-2.5 font-medium text-gray-900">
                    {item.parent_id && <span className="mr-1 text-gray-300">└</span>}
                    {item.category_name}
                  </td>
                  <td className="px-2 py-2.5 font-mono text-gray-600">{item.category_code}</td>
                  <td className="px-2 py-2.5 text-gray-600">{item.department_code ?? '-'}</td>
                  <td className="px-2 py-2.5 tabular-nums text-gray-600">{item.display_order}</td>
                  <td className="px-2 py-2.5 tabular-nums text-gray-600">{formatNumber(item.faq_count)}</td>
                  <td className="px-2 py-2.5">
                    {item.is_active ? (
                      <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700">사용</Badge>
                    ) : (
                      <Badge>미사용</Badge>
                    )}
                  </td>
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

      <CategoryModal
        open={!!editing}
        category={editing}
        categories={items}
        onClose={() => setEditing(null)}
        onSaved={(text) => {
          setEditing(null)
          setMessage({ tone: 'success', text })
          categories.reload()
        }}
      />
    </Card>
  )
}

function CategoryModal({ open, category, categories, onClose, onSaved }) {
  const isEdit = !!category?.id
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [initialized, setInitialized] = useState(null)

  // 모달이 열릴 때마다 대상 카테고리 값으로 폼을 초기화합니다.
  if (open && initialized !== (category?.id ?? 'new')) {
    setInitialized(category?.id ?? 'new')
    setError(null)
    setForm({
      category_name: category?.category_name ?? '',
      category_code: category?.category_code ?? '',
      parent_id: category?.parent_id ?? '',
      department_code: category?.department_code ?? '',
      display_order: category?.display_order ?? 0,
      is_active: category?.is_active ?? true,
    })
  }
  if (!open && initialized !== null) setInitialized(null)

  const set = (patch) => setForm((prev) => ({ ...prev, ...patch }))

  const save = async () => {
    if (!form.category_name.trim()) return setError({ message: '카테고리명을 입력해주세요.' })
    if (!form.category_code.trim()) return setError({ message: '카테고리 코드를 입력해주세요.' })

    const body = {
      category_name: form.category_name.trim(),
      category_code: form.category_code.trim(),
      parent_id: form.parent_id || null,
      department_code: form.department_code.trim() || null,
      display_order: Number(form.display_order) || 0,
      is_active: form.is_active,
    }

    setSaving(true)
    setError(null)
    try {
      if (isEdit) await faqApi.updateCategory(category.id, body)
      else await faqApi.createCategory(body)
      onSaved?.(isEdit ? '카테고리를 수정했습니다.' : '카테고리를 등록했습니다.')
    } catch (err) {
      setError(err)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? '카테고리 수정' : '카테고리 추가'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving ? '저장 중…' : '저장'}
          </Button>
        </>
      }
    >
      {error && (
        <InlineNotice tone="error" className="mb-3">
          {describeError(error)}
        </InlineNotice>
      )}

      <div className="space-y-3">
        <Field label="카테고리명">
          <Input value={form.category_name} onChange={(event) => set({ category_name: event.target.value })} placeholder="학사" />
        </Field>
        <Field label="카테고리 코드" hint="전체 카테고리에서 중복될 수 없습니다.">
          <Input
            value={form.category_code}
            onChange={(event) => set({ category_code: event.target.value })}
            placeholder="ACADEMIC"
            className="font-mono"
          />
        </Field>
        <Field label="상위 카테고리">
          <Select value={form.parent_id} onChange={(event) => set({ parent_id: event.target.value })}>
            <option value="">(최상위)</option>
            {categories
              .filter((item) => item.id !== category?.id)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.category_name}
                </option>
              ))}
          </Select>
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="담당 부서 코드">
            <Input
              value={form.department_code}
              onChange={(event) => set({ department_code: event.target.value })}
              placeholder="ACAD_AFFAIRS"
            />
          </Field>
          <Field label="노출 순서" hint="값이 작을수록 먼저 노출됩니다.">
            <Input
              type="number"
              min={0}
              value={form.display_order}
              onChange={(event) => set({ display_order: event.target.value })}
            />
          </Field>
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={(event) => set({ is_active: event.target.checked })}
            className="h-4 w-4 rounded border-gray-300 text-kmu-800 focus:ring-kmu-600"
          />
          사용함
        </label>
      </div>
    </Modal>
  )
}

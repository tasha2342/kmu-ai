import { useEffect, useState } from 'react'
import { Badge, Button, Field, InlineNotice, Input, Modal, Select, Textarea } from '../../components/ui.jsx'
import { faq as faqApi } from '../../lib/endpoints.js'
import { describeError } from '../../lib/api.js'
import {
  FAQ_STATUS_LABEL,
  FAQ_VISIBILITY_LABEL,
  LANGUAGE_LABEL,
  VECTOR_STATUS_LABEL,
  VECTOR_STATUS_STYLE,
} from '../../lib/constants.js'
import { formatDateTime } from '../../lib/format.js'

const EMPTY = {
  category_id: '',
  question: '',
  answer: '',
  aliases: '',
  tags: '',
  source_url: '',
  department_code: '',
  visibility: 'public',
  status: 'draft',
  language: 'ko',
}

/** FAQ 생성·수정 모달입니다. */
export default function FaqEditorModal({ open, faq, categories = [], onClose, onSaved }) {
  const isEdit = !!faq?.id
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [indexInfo, setIndexInfo] = useState(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    setIndexInfo(null)
    setForm({
      ...EMPTY,
      category_id: faq?.category_id ?? categories[0]?.id ?? '',
      question: faq?.question ?? '',
      answer: faq?.answer ?? '',
      aliases: (faq?.question_aliases_json ?? []).join('\n'),
      tags: (faq?.tags_json ?? []).join(', '),
      source_url: faq?.source_url ?? '',
      department_code: faq?.department_code ?? '',
      visibility: faq?.visibility ?? 'public',
      status: faq?.status ?? 'draft',
      language: faq?.language ?? 'ko',
    })

    // 색인 상태는 상세 조회로만 알 수 있어 편집 시 함께 불러옵니다. 실패해도 편집은 계속 가능합니다.
    if (faq?.id) {
      faqApi
        .info(faq.id)
        .then((detail) => setIndexInfo(detail?.index ?? null))
        .catch(() => setIndexInfo(null))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, faq?.id])

  const set = (patch) => setForm((prev) => ({ ...prev, ...patch }))

  const save = async () => {
    if (!form.category_id) return setError({ message: '카테고리를 선택해주세요.' })
    if (!form.question.trim()) return setError({ message: '대표 질문을 입력해주세요.' })
    if (!form.answer.trim()) return setError({ message: '답변을 입력해주세요.' })

    const body = {
      category_id: form.category_id,
      question: form.question.trim(),
      answer: form.answer.trim(),
      question_aliases_json: splitLines(form.aliases),
      tags_json: splitList(form.tags),
      source_url: form.source_url.trim() || null,
      department_code: form.department_code.trim() || null,
      visibility: form.visibility,
      status: form.status,
      language: form.language,
    }

    setSaving(true)
    setError(null)
    try {
      const result = isEdit ? await faqApi.update(faq.id, body) : await faqApi.create(body)
      const suffix = result?.index_warning ? ` (색인 경고: ${result.index_warning})` : result?.indexed ? ' (색인 완료)' : ''
      onSaved?.(`${isEdit ? 'FAQ를 수정했습니다.' : 'FAQ를 등록했습니다.'}${suffix}`)
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
      wide
      title={isEdit ? 'FAQ 수정' : 'FAQ 추가'}
      description="공개(published) 상태로 저장하면 저장 직후 색인을 시도합니다."
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

      {indexInfo && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-[11px] text-gray-500">
          <Badge className={VECTOR_STATUS_STYLE[indexInfo.vector_status]}>
            {VECTOR_STATUS_LABEL[indexInfo.vector_status] ?? indexInfo.vector_status}
          </Badge>
          <span>임베딩 모델 {indexInfo.embedding_model}</span>
          {indexInfo.indexed_at && <span>· 색인 {formatDateTime(indexInfo.indexed_at)}</span>}
          {indexInfo.is_stale && <span className="text-amber-700">· 원문이 변경되어 재색인이 필요합니다</span>}
        </div>
      )}

      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="카테고리">
            <Select value={form.category_id} onChange={(event) => set({ category_id: event.target.value })}>
              <option value="">선택하세요</option>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.category_name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="상태">
            <Select value={form.status} onChange={(event) => set({ status: event.target.value })}>
              {Object.entries(FAQ_STATUS_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="공개 범위">
            <Select value={form.visibility} onChange={(event) => set({ visibility: event.target.value })}>
              {Object.entries(FAQ_VISIBILITY_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <Field label="대표 질문" hint="유사도 검색의 색인 대상입니다.">
          <Input
            value={form.question}
            onChange={(event) => set({ question: event.target.value })}
            placeholder="수강신청 기간은 언제인가요?"
          />
        </Field>

        <Field label="답변">
          <Textarea rows={6} value={form.answer} onChange={(event) => set({ answer: event.target.value })} />
        </Field>

        <Field label="유사 질문" hint="한 줄에 하나씩 입력합니다. 구어체 질의의 검색 정확도를 높입니다.">
          <Textarea
            rows={3}
            value={form.aliases}
            onChange={(event) => set({ aliases: event.target.value })}
            placeholder={'수강신청 언제야\n수강신청 일정 알려줘'}
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="태그" hint="쉼표로 구분합니다.">
            <Input value={form.tags} onChange={(event) => set({ tags: event.target.value })} placeholder="수강신청, 학사일정" />
          </Field>
          <Field label="언어">
            <Select value={form.language} onChange={(event) => set({ language: event.target.value })}>
              {Object.entries(LANGUAGE_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="원문 URL">
            <Input
              value={form.source_url}
              onChange={(event) => set({ source_url: event.target.value })}
              placeholder="https://www.kmu.ac.kr/notice/1234"
            />
          </Field>
          <Field label="담당 부서 코드">
            <Input
              value={form.department_code}
              onChange={(event) => set({ department_code: event.target.value })}
              placeholder="ACAD_AFFAIRS"
            />
          </Field>
        </div>
      </div>
    </Modal>
  )
}

function splitLines(value) {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function splitList(value) {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

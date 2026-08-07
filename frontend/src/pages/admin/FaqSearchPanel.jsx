import { useState } from 'react'
import { Button, Card, EmptyState, ErrorNotice, Field, Input, Select } from '../../components/ui.jsx'
import { faq as faqApi } from '../../lib/endpoints.js'
import { LANGUAGE_LABEL } from '../../lib/constants.js'
import { formatMillis } from '../../lib/format.js'

/**
 * FAQ 유사도 검색 테스트 패널입니다. (POST /v1/faq/search)
 * 챗봇이 실제로 어떤 FAQ를 근거로 삼는지 미리 확인할 때 사용합니다.
 */
export default function FaqSearchPanel({ enabled, categories }) {
  const [form, setForm] = useState({ query: '', top_k: 5, score_threshold: '', language: '', category_code: '' })
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const set = (patch) => setForm((prev) => ({ ...prev, ...patch }))

  const search = async () => {
    if (!form.query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const data = await faqApi.search({
        query: form.query.trim(),
        top_k: Number(form.top_k) || 5,
        score_threshold: form.score_threshold === '' ? null : Number(form.score_threshold),
        language: form.language || null,
        category_code: form.category_code || null,
      })
      setResult(data)
    } catch (err) {
      setResult(null)
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  const categoryList = categories.data?.categories ?? []

  return (
    <Card
      title="유사도 검색 테스트"
      description="질문을 입력하면 챗봇이 근거로 사용할 FAQ 후보와 유사도 점수를 확인할 수 있습니다."
      bodyClassName="pt-3"
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="질문" className="sm:col-span-2 lg:col-span-2">
          <Input
            value={form.query}
            onChange={(event) => set({ query: event.target.value })}
            onKeyDown={(event) => event.key === 'Enter' && search()}
            placeholder="수강신청 언제 하나요?"
          />
        </Field>
        <Field label="결과 수 (top_k)">
          <Input type="number" min={1} max={50} value={form.top_k} onChange={(event) => set({ top_k: event.target.value })} />
        </Field>
        <Field label="최소 유사도" hint="비우면 챗봇 설정값을 사용합니다.">
          <Input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={form.score_threshold}
            onChange={(event) => set({ score_threshold: event.target.value })}
            placeholder="0.35"
          />
        </Field>
        <Field label="카테고리 코드">
          <Select value={form.category_code} onChange={(event) => set({ category_code: event.target.value })}>
            <option value="">전체</option>
            {categoryList.map((category) => (
              <option key={category.id} value={category.category_code}>
                {category.category_name}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Select
          value={form.language}
          onChange={(event) => set({ language: event.target.value })}
          className="w-auto py-1.5 text-xs"
          aria-label="언어 필터"
        >
          <option value="">모든 언어</option>
          {Object.entries(LANGUAGE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
        <Button onClick={search} disabled={!enabled || loading || !form.query.trim()}>
          {loading ? '검색 중…' : '검색'}
        </Button>
      </div>

      <div className="mt-4">
        {error && <ErrorNotice error={error} onRetry={search} />}

        {!error && result && (
          <>
            <p className="mb-2 text-xs text-gray-500">
              &quot;{result.query}&quot; · {result.total_count}건
              {result.score_threshold != null && ` · 최소 유사도 ${result.score_threshold}`} ·{' '}
              {formatMillis(result.latency_ms)}
            </p>
            {result.results?.length === 0 ? (
              <EmptyState
                title="검색 결과가 없습니다"
                description="색인이 되어 있지 않거나 유사도 임계값보다 낮은 후보만 존재합니다. 색인 동기화를 먼저 실행해보세요."
              />
            ) : (
              <ol className="space-y-2">
                {result.results.map((item) => (
                  <li key={item.faq_id} className="rounded-lg border border-gray-200 px-3 py-2.5">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="text-sm font-medium text-gray-900">{item.question}</p>
                      <span className="text-xs tabular-nums text-gray-500">유사도 {item.score?.toFixed(3)}</span>
                    </div>
                    {item.answer && <p className="mt-1 line-clamp-3 text-xs leading-relaxed text-gray-600">{item.answer}</p>}
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 text-[11px] text-gray-400">
                      {item.category_code && <span>{item.category_code}</span>}
                      {item.department_code && <span>· {item.department_code}</span>}
                      {item.tags?.length > 0 && <span>· {item.tags.map((tag) => `#${tag}`).join(' ')}</span>}
                      {item.source_url && (
                        <a
                          href={item.source_url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-kmu-700 underline decoration-dotted underline-offset-2"
                        >
                          원문 보기
                        </a>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}

        {!error && !result && !loading && (
          <EmptyState title="검색어를 입력해주세요" description="챗봇 응답의 근거 후보를 미리 확인할 수 있습니다." />
        )}
      </div>
    </Card>
  )
}

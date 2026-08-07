import { useState } from 'react'
import { UNANSWERED_REASON_LABEL } from '../lib/constants.js'

/**
 * 응답 근거(sources) 표시입니다. (KAI-REQ-015)
 *
 * - 근거가 있으면 접을 수 있는 목록으로 출처 FAQ와 유사도 점수를 보여줍니다.
 * - 근거 없이 생성된 답변은 그 사실이 드러나도록 별도 안내를 노출합니다.
 * - 미응답(is_answered=false)이면 사유를 함께 표시합니다.
 */
export default function ChatSources({ sources, isAnswered = true, unansweredReason, streaming = false }) {
  const [open, setOpen] = useState(false)
  const list = sources ?? []

  if (streaming && list.length === 0) return null

  if (list.length === 0) {
    if (isAnswered === false) {
      return (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
          <span className="font-medium">답변 근거를 찾지 못했습니다.</span>
          {unansweredReason && (
            <span className="ml-1">사유: {UNANSWERED_REASON_LABEL[unansweredReason] ?? unansweredReason}</span>
          )}
          <span className="ml-1 text-amber-700/80">담당 부서에 직접 문의하시면 더 정확한 안내를 받을 수 있습니다.</span>
        </div>
      )
    }
    return (
      <p className="mt-2 text-[11px] text-gray-400">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-gray-300 align-middle" aria-hidden="true" />
        <span className="ml-1.5 align-middle">등록된 FAQ 근거 없이 생성된 답변입니다.</span>
      </p>
    )
  }

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-kmu-700 transition-colors hover:bg-kmu-50"
        aria-expanded={open}
      >
        <span className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden="true">
          ▸
        </span>
        답변 근거 {list.length}건
      </button>

      {open && (
        <ol className="mt-1.5 space-y-1.5 border-l-2 border-kmu-100 pl-3">
          {list.map((source, index) => (
            <li key={`${source.source_id ?? index}`} className="text-[11px] leading-relaxed text-gray-600">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium text-gray-800">{source.question || '(제목 없음)'}</span>
                {typeof source.score === 'number' && (
                  <span className="tabular-nums text-gray-400">유사도 {source.score.toFixed(2)}</span>
                )}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-gray-400">
                <span>{(source.source_type ?? 'faq').toUpperCase()}</span>
                {source.category_code && <span>· {source.category_code}</span>}
                {source.department_code && <span>· {source.department_code}</span>}
                {source.source_url && (
                  <a
                    href={source.source_url}
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
    </div>
  )
}

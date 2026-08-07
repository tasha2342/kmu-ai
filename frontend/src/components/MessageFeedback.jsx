import { useState } from 'react'
import { chatbot } from '../lib/endpoints.js'
import { describeError } from '../lib/api.js'
import { Button, Textarea } from './ui.jsx'

/**
 * 챗봇(assistant) 메시지에 대한 만족도 피드백입니다. (POST /v1/chatbot/feedback)
 * 평점 1~5와 선택 코멘트를 보냅니다.
 */
export default function MessageFeedback({ messageId, submitted, onSubmitted }) {
  const [rating, setRating] = useState(0)
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  if (!messageId) return null

  if (submitted) {
    return <p className="mt-2 text-[11px] text-gray-400">평가해 주셔서 감사합니다. (★ {submitted}점)</p>
  }

  const submit = async (value, comment) => {
    setSaving(true)
    setError(null)
    try {
      await chatbot.addFeedback({
        message_id: messageId,
        rating: value,
        feedback_text: comment?.trim() ? comment.trim() : null,
      })
      onSubmitted?.(value)
      setOpen(false)
    } catch (err) {
      setError(err)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-gray-400">이 답변이 도움이 되었나요?</span>
        <div className="flex items-center gap-0.5" role="group" aria-label="만족도 평가">
          {[1, 2, 3, 4, 5].map((value) => (
            <button
              key={value}
              type="button"
              disabled={saving}
              onMouseEnter={() => setRating(value)}
              onMouseLeave={() => setRating(0)}
              onFocus={() => setRating(value)}
              onBlur={() => setRating(0)}
              onClick={() => {
                setRating(value)
                setOpen(true)
              }}
              className={`px-0.5 text-sm leading-none transition-colors ${
                value <= rating ? 'text-amber-500' : 'text-gray-300 hover:text-amber-300'
              }`}
              aria-label={`${value}점`}
            >
              ★
            </button>
          ))}
        </div>
      </div>

      {open && (
        <div className="mt-2 rounded-lg border border-gray-200 bg-gray-50 p-2.5">
          <Textarea
            rows={2}
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={2000}
            placeholder="어떤 점이 좋았거나 아쉬웠는지 알려주세요. (선택)"
            className="text-xs"
          />
          {error && <p className="mt-1.5 text-[11px] text-rose-600">{describeError(error)}</p>}
          <div className="mt-2 flex justify-end gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)} disabled={saving}>
              취소
            </Button>
            <Button size="sm" onClick={() => submit(rating || 5, text)} disabled={saving}>
              {saving ? '보내는 중…' : `${rating || 5}점으로 평가`}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

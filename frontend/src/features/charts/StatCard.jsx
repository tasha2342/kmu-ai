/**
 * 단일 수치를 보여주는 통계 타일입니다. 플롯이 없으므로 색은 잉크 토큰만 사용합니다.
 */
export default function StatCard({ label, value, unit, hint, tone = 'default', loading = false }) {
  const tones = {
    default: 'text-gray-900',
    warning: 'text-amber-600',
    danger: 'text-rose-600',
    good: 'text-emerald-600',
  }
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3.5">
      <p className="text-xs text-gray-500">{label}</p>
      {loading ? (
        <div className="mt-2 h-7 w-20 animate-pulse rounded bg-gray-100" />
      ) : (
        <p className={`mt-1 text-2xl font-semibold tabular-nums ${tones[tone]}`}>
          {value}
          {unit && <span className="ml-0.5 text-sm font-medium text-gray-400">{unit}</span>}
        </p>
      )}
      {hint && <p className="mt-1 text-[11px] text-gray-400">{hint}</p>}
    </div>
  )
}

import { Button, Input } from '../../components/ui.jsx'
import { daysAgo, today } from '../../lib/format.js'

const PRESETS = [
  { label: '최근 7일', days: 6 },
  { label: '최근 30일', days: 29 },
  { label: '최근 90일', days: 89 },
]

/** 통계·로그 화면 상단의 기간 필터입니다. (한 줄 배치) */
export default function DateRangeFilter({ value, onChange, extra }) {
  const set = (patch) => onChange({ ...value, ...patch })

  return (
    <div className="mb-4 flex flex-wrap items-end gap-2">
      <label className="flex items-center gap-1.5 text-xs text-gray-500">
        시작
        <Input
          type="date"
          value={value.start_date ?? ''}
          max={value.end_date || undefined}
          onChange={(event) => set({ start_date: event.target.value })}
          className="w-auto py-1.5 text-xs"
        />
      </label>
      <label className="flex items-center gap-1.5 text-xs text-gray-500">
        종료
        <Input
          type="date"
          value={value.end_date ?? ''}
          min={value.start_date || undefined}
          onChange={(event) => set({ end_date: event.target.value })}
          className="w-auto py-1.5 text-xs"
        />
      </label>
      <div className="flex gap-1">
        {PRESETS.map((preset) => (
          <Button
            key={preset.label}
            variant="secondary"
            size="sm"
            onClick={() => onChange({ ...value, start_date: daysAgo(preset.days), end_date: today() })}
          >
            {preset.label}
          </Button>
        ))}
      </div>
      {extra}
    </div>
  )
}

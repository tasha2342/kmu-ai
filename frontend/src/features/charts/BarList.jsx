import { MAGNITUDE } from './palette.js'
import { formatNumber } from '../../lib/format.js'

/**
 * 가로 막대 목록입니다. 항목 이름은 축(왼쪽 라벨)이 담당하므로 색을 순환시키지 않고
 * 단일 색상으로 크기(magnitude)만 나타냅니다.
 *
 * @param {Array} items  [{ label, value, sub? , color? }]
 */
export default function BarList({ items = [], valueSuffix = '', emptyText = '표시할 데이터가 없습니다.', maxRows }) {
  const rows = maxRows ? items.slice(0, maxRows) : items
  if (!rows.length) {
    return <div className="flex h-32 items-center justify-center text-xs text-gray-400">{emptyText}</div>
  }

  const max = Math.max(...rows.map((item) => Number(item.value) || 0), 1)

  return (
    <ul className="space-y-2">
      {rows.map((item, index) => {
        const value = Number(item.value) || 0
        const ratio = Math.max(0.015, value / max)
        return (
          <li key={`${item.label}-${index}`} className="group">
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <span className="truncate text-xs text-gray-700" title={item.label}>
                {item.label}
              </span>
              <span className="shrink-0 text-xs font-medium tabular-nums text-gray-900">
                {formatNumber(value)}
                {valueSuffix}
                {item.sub && <span className="ml-1 font-normal text-gray-400">{item.sub}</span>}
              </span>
            </div>
            {/* 막대 끝만 둥글게 처리해 기준선에 붙어 있음을 유지합니다. */}
            <div className="h-2 w-full overflow-hidden rounded-sm bg-gray-100">
              <div
                className="h-full rounded-r-[4px] transition-[width] duration-300"
                style={{ width: `${ratio * 100}%`, backgroundColor: item.color || MAGNITUDE }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

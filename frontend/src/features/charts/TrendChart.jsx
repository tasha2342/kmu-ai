import { useMemo, useState } from 'react'
import { AXIS_TEXT, GRID, SERIES } from './palette.js'
import { useElementWidth } from './useElementWidth.js'
import { formatNumber } from '../../lib/format.js'

const PADDING = { top: 16, right: 14, bottom: 26, left: 44 }

/**
 * 일자별 추이를 그리는 다계열 라인 차트입니다. (의존성 없이 SVG로 직접 그립니다)
 *
 * @param {Array}  data     [{ date, ...values }]
 * @param {Array}  series   [{ key, label, format? }] — 최대 3계열
 * @param {number} height   플롯 높이(px)
 */
export default function TrendChart({ data = [], series = [], height = 200, valueLabel = '건' }) {
  const [wrapRef, width] = useElementWidth()
  const [hover, setHover] = useState(null)

  const points = useMemo(() => data.filter(Boolean), [data])
  const innerWidth = Math.max(60, width - PADDING.left - PADDING.right)
  const innerHeight = height

  const maxValue = useMemo(() => {
    const values = points.flatMap((row) => series.map((s) => Number(row[s.key] ?? 0)))
    const max = values.length ? Math.max(...values) : 0
    return max > 0 ? max : 1
  }, [points, series])

  const ticks = useMemo(() => niceTicks(maxValue), [maxValue])
  const scaleMax = ticks[ticks.length - 1] || 1

  const xAt = (index) =>
    PADDING.left + (points.length <= 1 ? innerWidth / 2 : (innerWidth * index) / (points.length - 1))
  const yAt = (value) => PADDING.top + innerHeight - (innerHeight * Number(value ?? 0)) / scaleMax

  if (!points.length) {
    return (
      <div className="flex h-40 items-center justify-center text-xs text-gray-400">
        표시할 데이터가 없습니다.
      </div>
    )
  }

  const totalHeight = PADDING.top + innerHeight + PADDING.bottom
  // x축 라벨이 겹치지 않도록 일정 간격으로만 노출합니다.
  const labelStep = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(innerWidth / 78))))

  const handleMove = (event) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const x = event.clientX - rect.left
    const ratio = (x - PADDING.left) / innerWidth
    const index = Math.round(ratio * (points.length - 1))
    setHover(Math.min(points.length - 1, Math.max(0, index)))
  }

  return (
    <div className="w-full">
      {series.length >= 2 && (
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {series.map((s, index) => (
            <span key={s.key} className="inline-flex items-center gap-1.5 text-[11px] text-gray-600">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: SERIES[index] }}
                aria-hidden="true"
              />
              {s.label}
            </span>
          ))}
        </div>
      )}

      <div ref={wrapRef} className="relative w-full">
        <svg
          width={width}
          height={totalHeight}
          role="img"
          aria-label={`일자별 ${series.map((s) => s.label).join(', ')} 추이`}
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
        >
          {/* 격자·눈금 (물러나 있어야 합니다) */}
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={PADDING.left} x2={width - PADDING.right} y1={yAt(tick)} y2={yAt(tick)} stroke={GRID} strokeWidth="1" />
              <text x={PADDING.left - 8} y={yAt(tick) + 3.5} textAnchor="end" fontSize="10" fill={AXIS_TEXT}>
                {compact(tick)}
              </text>
            </g>
          ))}

          {/* x축 라벨 */}
          {points.map((row, index) =>
            index % labelStep === 0 || index === points.length - 1 ? (
              <text key={row.date ?? index} x={xAt(index)} y={totalHeight - 8} textAnchor="middle" fontSize="10" fill={AXIS_TEXT}>
                {shortDate(row.date)}
              </text>
            ) : null
          )}

          {/* 호버 크로스헤어 */}
          {hover !== null && (
            <line
              x1={xAt(hover)}
              x2={xAt(hover)}
              y1={PADDING.top}
              y2={PADDING.top + innerHeight}
              stroke="#94a3b8"
              strokeWidth="1"
              strokeDasharray="3 3"
            />
          )}

          {/* 계열 */}
          {series.map((s, index) => {
            const color = SERIES[index] ?? SERIES[0]
            const path = points.map((row, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(row[s.key])}`).join(' ')
            const area = `${path} L${xAt(points.length - 1)},${PADDING.top + innerHeight} L${xAt(0)},${PADDING.top + innerHeight} Z`
            return (
              <g key={s.key}>
                {series.length === 1 && <path d={area} fill={color} opacity="0.08" />}
                <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
                {(points.length <= 40 || hover !== null) &&
                  points.map((row, i) =>
                    points.length <= 40 || i === hover ? (
                      <circle
                        key={`${s.key}-${i}`}
                        cx={xAt(i)}
                        cy={yAt(row[s.key])}
                        r={i === hover ? 4.5 : 2.5}
                        fill={color}
                        stroke="#ffffff"
                        strokeWidth="2"
                      />
                    ) : null
                  )}
              </g>
            )
          })}
        </svg>

        {hover !== null && (
          <div
            className="pointer-events-none absolute z-10 min-w-[8.5rem] -translate-x-1/2 rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[11px] shadow-lg"
            style={{
              left: Math.min(Math.max(xAt(hover), 74), Math.max(74, width - 74)),
              top: 0,
            }}
          >
            <p className="mb-1 font-medium text-gray-900">{points[hover].date}</p>
            {series.map((s, index) => (
              <p key={s.key} className="flex items-center justify-between gap-3 text-gray-600">
                <span className="inline-flex items-center gap-1.5">
                  <span
                    className="inline-block h-1.5 w-1.5 rounded-full"
                    style={{ backgroundColor: SERIES[index] }}
                    aria-hidden="true"
                  />
                  {s.label}
                </span>
                <span className="tabular-nums font-medium text-gray-900">
                  {formatNumber(points[hover][s.key])}
                  {valueLabel}
                </span>
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function niceTicks(max) {
  const rough = max / 4
  const magnitude = 10 ** Math.floor(Math.log10(rough || 1))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) || magnitude * 10
  const top = Math.ceil(max / step) * step
  const result = []
  for (let value = 0; value <= top + 1e-9; value += step) result.push(Math.round(value * 100) / 100)
  return result
}

function compact(value) {
  if (value >= 10000) return `${Math.round(value / 1000)}k`
  return String(value)
}

function shortDate(value) {
  if (!value) return ''
  const parts = String(value).split('-')
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : String(value)
}

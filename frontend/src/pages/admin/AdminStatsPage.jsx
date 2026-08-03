import { useState } from 'react'
import DateRangeFilter from './DateRangeFilter.jsx'
import StatCard from '../../features/charts/StatCard.jsx'
import TrendChart from '../../features/charts/TrendChart.jsx'
import BarList from '../../features/charts/BarList.jsx'
import { ORDINAL_5 } from '../../features/charts/palette.js'
import { Button, Card, ErrorNotice, LoadingBlock } from '../../components/ui.jsx'
import { chatbotAdmin } from '../../lib/endpoints.js'
import { useApiResource } from '../../lib/useApiResource.js'
import { useAuth } from '../../lib/useAuth.js'
import { CHAT_INTENT_LABEL } from '../../lib/constants.js'
import { daysAgo, formatDecimal, formatMillis, formatNumber, formatPercent, today } from '../../lib/format.js'

/** 챗봇 이용 통계 대시보드입니다. (KAI-REQ-031 / 033) */
export default function AdminStatsPage() {
  const session = useAuth()
  const enabled = !!session
  const [range, setRange] = useState({ start_date: daysAgo(29), end_date: today() })
  const deps = [range.start_date, range.end_date]

  const stats = useApiResource(() => chatbotAdmin.stats(range), deps, { enabled })
  const keywords = useApiResource(() => chatbotAdmin.keywordStats({ ...range, top_n: 15 }), deps, { enabled })
  const intents = useApiResource(() => chatbotAdmin.intentStats(range), deps, { enabled })
  const feedback = useApiResource(() => chatbotAdmin.feedbackStats(range), deps, { enabled })

  const reloadAll = () => {
    stats.reload()
    keywords.reload()
    intents.reload()
    feedback.reload()
  }

  const s = stats.data

  return (
    <div className="space-y-4">
      <DateRangeFilter
        value={range}
        onChange={setRange}
        extra={
          <Button variant="secondary" size="sm" onClick={reloadAll}>
            새로고침
          </Button>
        }
      />

      {stats.error && <ErrorNotice error={stats.error} onRetry={stats.reload} />}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="총 질문 수" value={formatNumber(s?.total_questions)} unit="건" loading={stats.loading} />
        <StatCard label="총 세션 수" value={formatNumber(s?.total_sessions)} unit="개" loading={stats.loading} />
        <StatCard label="활성 사용자" value={formatNumber(s?.active_users)} unit="명" loading={stats.loading} />
        <StatCard
          label="미응답률"
          value={formatPercent(s?.unanswered_rate)}
          hint={s ? `미응답 ${formatNumber(s.unanswered_count)}건` : undefined}
          tone={s && s.unanswered_rate >= 10 ? 'danger' : s && s.unanswered_rate >= 5 ? 'warning' : 'default'}
          loading={stats.loading}
        />
        <StatCard
          label="평균 만족도"
          value={s?.average_rating != null ? formatDecimal(s.average_rating, 2) : '평가 없음'}
          unit={s?.average_rating != null ? '/ 5' : undefined}
          hint={s ? `평가 ${formatNumber(s.feedback_count)}건` : undefined}
          loading={stats.loading}
        />
        <StatCard label="평균 응답시간" value={formatMillis(s?.avg_response_time_ms)} loading={stats.loading} />
      </div>

      <Card title="일자별 질문·미응답 추이" description={s ? `${s.start_date} ~ ${s.end_date}` : undefined}>
        {stats.loading ? (
          <LoadingBlock />
        ) : stats.error ? (
          <p className="py-8 text-center text-xs text-gray-400">통계를 불러오지 못했습니다.</p>
        ) : (
          <TrendChart
            data={s?.daily_trend ?? []}
            series={[
              { key: 'question_count', label: '질문 수' },
              { key: 'unanswered_count', label: '미응답 수' },
            ]}
          />
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="인기 키워드"
          description={keywords.data ? `집계 질의 ${formatNumber(keywords.data.total_queries)}건` : undefined}
        >
          {keywords.loading ? (
            <LoadingBlock />
          ) : keywords.error ? (
            <ErrorNotice error={keywords.error} onRetry={keywords.reload} />
          ) : (
            <BarList
              items={(keywords.data?.keywords ?? []).map((item) => ({ label: item.keyword, value: item.count }))}
              valueSuffix="회"
              emptyText="집계된 키워드가 없습니다."
            />
          )}
        </Card>

        <Card
          title="의도별 질문 분포"
          description={
            intents.data
              ? `분류 ${formatNumber(intents.data.total_count)}건 · 미기록 ${formatNumber(intents.data.undetected_count)}건`
              : undefined
          }
        >
          {intents.loading ? (
            <LoadingBlock />
          ) : intents.error ? (
            <ErrorNotice error={intents.error} onRetry={intents.reload} />
          ) : (
            <BarList
              items={(intents.data?.intents ?? [])
                .slice()
                .sort((a, b) => b.count - a.count)
                .map((item) => ({
                  label: CHAT_INTENT_LABEL[item.intent] ?? item.intent,
                  value: item.count,
                  sub: `(${formatPercent(item.ratio)})`,
                }))}
              valueSuffix="건"
              emptyText="분류된 질문이 없습니다."
            />
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="만족도 평점 분포"
          description={
            feedback.data
              ? `평가 ${formatNumber(feedback.data.total_count)}건 · 평균 ${
                  feedback.data.average_rating != null ? formatDecimal(feedback.data.average_rating, 2) : '-'
                }`
              : undefined
          }
        >
          {feedback.loading ? (
            <LoadingBlock />
          ) : feedback.error ? (
            <ErrorNotice error={feedback.error} onRetry={feedback.reload} />
          ) : (
            <BarList
              items={(feedback.data?.distribution ?? []).map((item) => ({
                label: `★ ${item.rating}점`,
                value: item.count,
                sub: `(${formatPercent(item.ratio)})`,
                // 평점은 순서가 있는 값이므로 단일 색상의 5단계 명도 램프를 사용합니다.
                color: ORDINAL_5[Math.min(4, Math.max(0, item.rating - 1))],
              }))}
              valueSuffix="건"
              emptyText="등록된 평가가 없습니다."
            />
          )}
        </Card>

        <Card title="일자별 평균 만족도">
          {feedback.loading ? (
            <LoadingBlock />
          ) : feedback.error ? (
            <p className="py-8 text-center text-xs text-gray-400">만족도 통계를 불러오지 못했습니다.</p>
          ) : (
            <TrendChart
              data={feedback.data?.daily_trend ?? []}
              series={[{ key: 'average_rating', label: '평균 만족도' }]}
              valueLabel="점"
              height={160}
            />
          )}
        </Card>
      </div>
    </div>
  )
}

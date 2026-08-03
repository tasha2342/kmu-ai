import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../../lib/useAuth.js'
import { InlineNotice } from '../../components/ui.jsx'

const MENU = [
  { to: '/admin', label: '이용 통계', end: true },
  { to: '/admin/unanswered', label: '미응답 질문' },
  { to: '/admin/faq', label: 'FAQ 관리' },
  { to: '/admin/rag', label: 'RAG 관리' },
  { to: '/admin/masking', label: '마스킹 관리' },
  { to: '/admin/logs', label: '로그' },
  { to: '/admin/ingestion', label: '수집 작업' },
]

export default function AdminLayout() {
  const session = useAuth()

  return (
    <div className="mx-auto w-full max-w-[1400px] px-3 py-4 sm:px-5">
      <nav className="scrollbar-thin mb-4 flex gap-1 overflow-x-auto border-b border-gray-200 pb-px">
        {MENU.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `whitespace-nowrap rounded-t-lg border-b-2 px-3.5 py-2 text-sm transition-colors ${
                isActive
                  ? 'border-kmu-800 font-medium text-kmu-900'
                  : 'border-transparent text-gray-500 hover:text-gray-800'
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      {!session && (
        <InlineNotice tone="warning" className="mb-4">
          로그인하지 않아 관리자 데이터를 조회할 수 없습니다. 우측 상단의 <b>로그인</b> 버튼을 눌러 계명대학교 통합
          인증(realm <code className="font-mono">kmu-ai</code>)으로 로그인해주세요. 관리자 기능은{' '}
          <code className="font-mono">admin</code> 역할이 있어야 사용할 수 있습니다.
        </InlineNotice>
      )}

      <Outlet />
    </div>
  )
}

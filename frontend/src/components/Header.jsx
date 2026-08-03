import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../lib/useAuth.js'
import { isExpired } from '../lib/authStore.js'
import AuthPanel from './AuthPanel.jsx'

const NAV = [
  { to: '/', label: '챗봇 상담', end: true },
  { to: '/admin', label: '관리자' },
]

export default function Header() {
  const session = useAuth()
  const [panelOpen, setPanelOpen] = useState(false)
  const expired = session && isExpired(session)

  return (
    <>
      <header className="sticky top-0 z-30 border-b border-gray-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-3 sm:px-5">
          <NavLink to="/" className="flex shrink-0 items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-kmu-900 text-xs font-bold text-white">
              KMU
            </span>
            <span className="hidden text-sm font-semibold text-gray-900 sm:inline">계명대학교 AI 챗봇</span>
          </NavLink>

          <nav className="ml-1 flex items-center gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
                    isActive ? 'bg-kmu-50 font-medium text-kmu-800' : 'text-gray-500 hover:bg-gray-100 hover:text-gray-800'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPanelOpen(true)}
              className={`rounded-lg border px-2.5 py-1.5 text-xs transition-colors ${
                !session
                  ? 'border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100'
                  : expired
                    ? 'border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100'
                    : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {!session ? '로그인' : expired ? '세션 만료 — 다시 로그인' : session.username}
            </button>
          </div>
        </div>
      </header>

      <AuthPanel open={panelOpen} onClose={() => setPanelOpen(false)} />
    </>
  )
}

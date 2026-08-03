import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { beginLogin, completeLogin } from '../lib/authStore.js'
import { Button, InlineNotice } from '../components/ui.jsx'

/**
 * Keycloak 로그인 콜백 화면입니다. (`/auth/callback`)
 *
 * 인가 코드를 토큰으로 교환한 뒤 로그인을 시작한 화면으로 되돌려 보냅니다.
 * 교환은 한 번만 성공하므로(코드는 일회용) React StrictMode의 이중 실행을 ref로 막습니다.
 */
export default function AuthCallbackPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [error, setError] = useState(null)
  const exchanged = useRef(false)

  useEffect(() => {
    if (exchanged.current) return
    exchanged.current = true

    completeLogin(params)
      .then(({ returnTo }) => {
        // 인가 코드가 주소창에 남지 않도록 히스토리에서 콜백 URL을 치웁니다.
        navigate(returnTo.startsWith('/auth/callback') ? '/' : returnTo, { replace: true })
      })
      .catch((err) => setError(err.message))
  }, [params, navigate])

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-4 py-16">
      {error ? (
        <>
          <InlineNotice tone="error">{error}</InlineNotice>
          <div className="mt-3 flex gap-2">
            <Button onClick={() => beginLogin('/')}>다시 로그인</Button>
            <Button variant="secondary" onClick={() => navigate('/', { replace: true })}>
              챗봇으로 이동
            </Button>
          </div>
        </>
      ) : (
        <p className="text-center text-sm text-gray-500">로그인 처리 중입니다…</p>
      )}
    </main>
  )
}

import { useState } from 'react'
import {
  AUTH_MODE,
  KEYCLOAK_CLIENT_ID,
  KEYCLOAK_REALM,
  beginLogin,
  isExpired,
  signInWithToken,
  signOut,
} from '../lib/authStore.js'
import { useAuth } from '../lib/useAuth.js'
import { formatDateTime } from '../lib/format.js'
import { Button, InlineNotice, Modal, Textarea } from './ui.jsx'

/**
 * 로그인 패널입니다.
 *
 * 기본 경로는 Keycloak realm(`kmu-ai`) 로그인입니다. 버튼을 누르면 Keycloak 로그인 화면으로
 * 이동했다가 `/auth/callback`으로 돌아옵니다. (Authorization Code + PKCE)
 *
 * 그 아래 접어 둔 토큰 직접 입력은 개발·점검용입니다. 백엔드 API Key(`jd-` 접두사)로
 * 관리자 화면을 열어보거나, Keycloak이 내려간 상황에서 확인할 때 씁니다.
 */
export default function AuthPanel({ open, onClose }) {
  const session = useAuth()
  const [token, setToken] = useState('')
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [manualOpen, setManualOpen] = useState(AUTH_MODE === 'manual')

  const expired = session && isExpired(session)

  const startLogin = async () => {
    setError(null)
    try {
      await beginLogin(window.location.pathname + window.location.search)
    } catch (err) {
      setError(err.message)
    }
  }

  const applyToken = async () => {
    setSaving(true)
    setError(null)
    try {
      await signInWithToken(token)
      setToken('')
      onClose?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={session ? '로그인 정보' : '로그인'}
      description={`계명대학교 통합 인증(Keycloak realm ${KEYCLOAK_REALM})으로 로그인합니다.`}
      footer={
        <>
          {session && (
            <Button variant="danger" onClick={() => signOut()}>
              로그아웃
            </Button>
          )}
          <Button variant="secondary" onClick={onClose}>
            닫기
          </Button>
          {(!session || expired) && AUTH_MODE === 'keycloak' && (
            <Button onClick={startLogin}>{expired ? '다시 로그인' : 'Keycloak으로 로그인'}</Button>
          )}
        </>
      }
    >
      {!session && AUTH_MODE === 'keycloak' && (
        <InlineNotice tone="info">
          <span className="font-medium">Keycloak으로 로그인</span>을 누르면 인증 화면으로 이동합니다. 로그인하면 이
          화면으로 돌아와 챗봇과 관리자 기능을 바로 사용할 수 있습니다.
        </InlineNotice>
      )}

      {expired && (
        <InlineNotice tone="warning">
          세션이 만료되었습니다. 다시 로그인해주세요.
        </InlineNotice>
      )}

      {session && (
        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-xs text-gray-600">
          <p>
            로그인 계정: <span className="font-medium text-gray-900">{session.username}</span>
          </p>
          {session.roles?.length > 0 && <p className="mt-0.5">역할: {session.roles.join(', ')}</p>}
          {session.expires_at && (
            <p className={`mt-0.5 ${expired ? 'text-rose-600' : ''}`}>
              만료: {formatDateTime(new Date(session.expires_at).toISOString())}
              {expired && ' (만료됨)'}
            </p>
          )}
          {session.access_token_source === 'manual' && (
            <p className="mt-0.5 text-gray-400">직접 입력한 토큰이라 자동 갱신되지 않습니다.</p>
          )}
        </div>
      )}

      <div className="mt-4 border-t border-gray-200 pt-3">
        <button
          type="button"
          onClick={() => setManualOpen((prev) => !prev)}
          className="text-xs text-gray-500 underline-offset-2 hover:text-gray-700 hover:underline"
        >
          {manualOpen ? '토큰 직접 입력 접기' : '토큰 직접 입력 (개발·점검용)'}
        </button>

        {manualOpen && (
          <div className="mt-2">
            <Textarea
              rows={4}
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="eyJhbGciOi... 또는 jd-..."
              className="font-mono text-xs"
              spellCheck={false}
            />
            <div className="mt-2 flex items-center justify-between gap-2">
              <p className="text-[11px] text-gray-400">
                Bearer 액세스 토큰 또는 API Key(<code className="font-mono">jd-</code>)를 붙여넣습니다. public client{' '}
                <code className="font-mono">{KEYCLOAK_CLIENT_ID}</code> 로그인과 달리 자동 갱신되지 않습니다.
              </p>
              <Button variant="secondary" onClick={applyToken} disabled={saving || !token.trim()}>
                {saving ? '적용 중…' : '토큰 적용'}
              </Button>
            </div>
          </div>
        )}

        {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
      </div>
    </Modal>
  )
}

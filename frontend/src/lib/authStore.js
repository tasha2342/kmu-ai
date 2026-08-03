/**
 * 인증 토큰 보관소입니다.
 *
 * 계명대 전용 Keycloak realm(`kmu-ai`)에 **Authorization Code + PKCE**로 로그인합니다.
 * `kmu-ai-frontend`는 시크릿이 없는 public 클라이언트라 브라우저에서 직접 코드 교환을 합니다.
 * 비밀번호를 이 앱이 다루지 않고, Keycloak 세션을 그대로 쓰므로 SSO와 로그아웃이 정상 동작합니다.
 *
 * Keycloak 요청은 모두 nginx `/realms/` 프록시를 통해 **같은 출처로** 나갑니다.
 * 덕분에 CORS 설정이 필요 없고, Keycloak이 브라우저가 접속한 호스트명을 그대로 발급 주소로 씁니다.
 *
 * 액세스 토큰을 직접 붙여넣는 개발용 경로(`signInWithToken`)도 남겨 뒀습니다.
 * 백엔드 API Key(`jd-` 접두사)로 관리자 화면을 열어볼 때 씁니다.
 *
 * 세션 객체 형태
 *   { access_token, refresh_token?, id_token?, username, roles: string[], expires_at?: number(ms) }
 */

import { sha256 } from './sha256.js'

const STORAGE_KEY = 'kmu-ai-auth'
const PKCE_KEY = 'kmu-ai-auth-pkce'

/** 'keycloak' = Keycloak 로그인, 'manual' = 토큰 직접 입력만 허용 */
export const AUTH_MODE = 'keycloak'

/** nginx `/realms/` 프록시를 통해 접근합니다. (kmu-ai-keycloak:8080) */
export const KEYCLOAK_REALM = 'kmu-ai'
/** PKCE를 사용하는 public 클라이언트입니다. */
export const KEYCLOAK_CLIENT_ID = 'kmu-ai-frontend'

/** 로그인 후 돌아올 경로입니다. Keycloak 클라이언트의 redirect URI에 등록돼 있어야 합니다. */
export const AUTH_CALLBACK_PATH = '/auth/callback'

/** 관리자 화면 접근에 필요한 역할 이름입니다. (realm 역할 `admin`) */
const ADMIN_ROLE_NAMES = ['admin']

const listeners = new Set()
let refreshPromise = null

// ===== 저장소 =====

function read() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function write(session) {
  try {
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 프라이빗 모드 등 localStorage 사용 불가 환경에서는 메모리 상태만 유지합니다.
  }
  cached = session
  listeners.forEach((fn) => fn(session))
}

let cached = read()

export function getSession() {
  return cached
}

export function getToken() {
  return cached?.access_token ?? null
}

export function isAuthenticated() {
  return !!getToken()
}

export function subscribe(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

// ===== JWT 파싱 (표시용) =====

/**
 * JWT 페이로드를 디코딩합니다. 서명은 검증하지 않으며 화면 표시 용도로만 사용합니다.
 * 실제 권한 검사는 백엔드가 수행합니다.
 */
export function decodeJwt(token) {
  try {
    const payload = token.split('.')[1]
    if (!payload) return null
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
    const json = decodeURIComponent(
      atob(normalized)
        .split('')
        .map((c) => `%${`00${c.charCodeAt(0).toString(16)}`.slice(-2)}`)
        .join('')
    )
    return JSON.parse(json)
  } catch {
    return null
  }
}

function buildSession(accessToken, extra = {}) {
  const claims = decodeJwt(accessToken)
  const roles = [
    ...(claims?.realm_access?.roles ?? []),
    ...Object.values(claims?.resource_access ?? {}).flatMap((entry) => entry?.roles ?? []),
  ]
  return {
    access_token: accessToken,
    refresh_token: extra.refresh_token ?? null,
    username: claims?.preferred_username || claims?.name || claims?.sub || extra.username || '(알 수 없음)',
    roles,
    expires_at: claims?.exp ? claims.exp * 1000 : null,
    ...extra,
    // extra가 위 값을 덮어쓰지 않도록 핵심 필드는 마지막에 다시 고정합니다.
    access_token_source: extra.access_token_source ?? AUTH_MODE,
  }
}

export function hasAdminRole(session = getSession()) {
  if (!session) return false
  // 역할 정보가 없는 토큰(API Key 등)은 판단할 수 없으므로 막지 않고 백엔드 응답에 맡깁니다.
  if (!session.roles?.length) return true
  return session.roles.some((role) => ADMIN_ROLE_NAMES.includes(role))
}

export function isExpired(session = getSession()) {
  if (!session?.expires_at) return false
  return session.expires_at <= Date.now()
}

// ===== tokenProvider 구현체 =====

/** 현재 사용 중: 사용자가 직접 붙여넣은 토큰을 그대로 사용합니다. */
const manualTokenProvider = {
  async login({ token }) {
    const trimmed = (token ?? '').trim()
    if (!trimmed) throw new Error('토큰을 입력해주세요.')
    return buildSession(trimmed)
  },
  async refresh() {
    // 직접 입력한 토큰은 갱신할 수 없습니다. 만료되면 다시 붙여넣어야 합니다.
    return null
  },
  async logout() {},
}

// ===== Keycloak (Authorization Code + PKCE) =====

const endpoint = (name) => `/realms/${KEYCLOAK_REALM}/protocol/openid-connect/${name}`

function redirectUri() {
  return `${window.location.origin}${AUTH_CALLBACK_PATH}`
}

/** URL-safe base64 (padding 없음) — PKCE 명세가 요구하는 형식입니다. */
function base64Url(bytes) {
  let binary = ''
  new Uint8Array(bytes).forEach((b) => {
    binary += String.fromCharCode(b)
  })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function randomString(byteLength = 32) {
  return base64Url(crypto.getRandomValues(new Uint8Array(byteLength)))
}

/**
 * PKCE `code_challenge`(S256)를 만듭니다.
 *
 * `crypto.subtle`은 보안 컨텍스트(HTTPS 또는 localhost)에서만 존재합니다. 평문 HTTP + IP로
 * 접속하면 undefined가 되므로, 그때는 동일한 결과를 내는 순수 JS 구현으로 넘어갑니다.
 */
async function challengeOf(verifier) {
  const input = new TextEncoder().encode(verifier)
  if (crypto?.subtle?.digest) {
    return base64Url(await crypto.subtle.digest('SHA-256', input))
  }
  return base64Url(sha256(input))
}

/**
 * Keycloak 로그인 화면으로 이동합니다. (돌아온 뒤 `completeLogin`이 이어받습니다)
 *
 * @param {string} returnTo 로그인 후 복귀할 앱 내부 경로
 */
export async function beginLogin(returnTo = window.location.pathname + window.location.search) {
  const verifier = randomString()
  const state = randomString(16)

  // 리다이렉트로 페이지가 통째로 날아가므로 verifier는 브라우저에 남겨 둬야 합니다.
  // sessionStorage를 쓰면 탭을 닫는 순간 사라져 localStorage보다 노출 시간이 짧습니다.
  sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state, returnTo }))

  const params = new URLSearchParams({
    client_id: KEYCLOAK_CLIENT_ID,
    redirect_uri: redirectUri(),
    response_type: 'code',
    scope: 'openid profile email',
    state,
    code_challenge: await challengeOf(verifier),
    code_challenge_method: 'S256',
  })
  window.location.assign(`${endpoint('auth')}?${params}`)
}

/**
 * 로그인 콜백에서 인가 코드를 토큰으로 교환합니다.
 *
 * @param {URLSearchParams} query 콜백 URL의 쿼리스트링
 * @returns {Promise<{ returnTo: string }>} 복귀할 경로
 */
export async function completeLogin(query) {
  const error = query.get('error')
  if (error) throw new Error(query.get('error_description') || `로그인이 취소되었습니다. (${error})`)

  const code = query.get('code')
  if (!code) throw new Error('인가 코드가 없습니다. 로그인을 다시 시도해주세요.')

  const stored = sessionStorage.getItem(PKCE_KEY)
  sessionStorage.removeItem(PKCE_KEY)
  if (!stored) throw new Error('로그인 요청 정보가 만료되었습니다. 로그인을 다시 시도해주세요.')

  const { verifier, state, returnTo } = JSON.parse(stored)
  // state 불일치는 다른 창에서 시작된 로그인이 끼어든 경우(CSRF 포함)이므로 교환하지 않습니다.
  if (query.get('state') !== state) throw new Error('로그인 상태 값이 일치하지 않습니다. 다시 시도해주세요.')

  const response = await fetch(endpoint('token'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: KEYCLOAK_CLIENT_ID,
      code,
      redirect_uri: redirectUri(),
      code_verifier: verifier,
    }),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(data?.error_description || '토큰 발급에 실패했습니다.')

  write(
    buildSession(data.access_token, {
      refresh_token: data.refresh_token ?? null,
      id_token: data.id_token ?? null,
      access_token_source: 'keycloak',
    })
  )
  return { returnTo: returnTo || '/' }
}

const keycloakTokenProvider = {
  async refresh(session) {
    if (!session?.refresh_token) return null
    const response = await fetch(endpoint('token'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'refresh_token',
        client_id: KEYCLOAK_CLIENT_ID,
        refresh_token: session.refresh_token,
      }),
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) return null
    return buildSession(data.access_token, {
      refresh_token: data.refresh_token ?? session.refresh_token,
      id_token: data.id_token ?? session.id_token ?? null,
      access_token_source: 'keycloak',
    })
  },
  /** Keycloak 세션까지 끊습니다. 이 호출로 페이지가 Keycloak으로 이동합니다. */
  async logout(session) {
    if (!session?.id_token) return
    const params = new URLSearchParams({
      id_token_hint: session.id_token,
      post_logout_redirect_uri: window.location.origin,
    })
    window.location.assign(`${endpoint('logout')}?${params}`)
  },
}

const providers = {
  manual: manualTokenProvider,
  keycloak: keycloakTokenProvider,
}

/** 현재 활성화된 토큰 공급자입니다. */
export const tokenProvider = providers[AUTH_MODE] ?? manualTokenProvider

// ===== 공개 API =====

/**
 * 액세스 토큰(또는 백엔드 API Key)을 직접 보관합니다. 개발·점검용 경로입니다.
 * 이 토큰은 만료돼도 자동 갱신되지 않습니다.
 */
export async function signInWithToken(token) {
  const session = await manualTokenProvider.login({ token })
  write({ ...session, access_token_source: 'manual' })
  return session
}

/** 세션을 발급한 방식에 맞는 공급자를 고릅니다. (직접 입력한 토큰은 갱신 대상이 아닙니다) */
function providerFor(session) {
  return providers[session?.access_token_source] ?? tokenProvider
}

/** 액세스 토큰이 만료되어 401을 받았을 때 한 번만 갱신을 시도합니다. */
export async function refreshAccessToken() {
  const session = read()
  if (!session) return null

  if (!refreshPromise) {
    refreshPromise = Promise.resolve(providerFor(session).refresh(session))
      .then((next) => {
        if (next) write(next)
        return next
      })
      .catch(() => null)
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

export function signOut() {
  const session = read()
  write(null)
  Promise.resolve(providerFor(session).logout?.(session)).catch(() => {})
}

/** api.js가 401 응답에서 호출합니다. 화면 갱신을 위해 세션만 비웁니다. */
export function clearToken() {
  write(null)
}

import { useSyncExternalStore } from 'react'
import { getSession, subscribe } from './authStore.js'

/** 현재 보관 중인 인증 세션을 구독합니다. 토큰이 없으면 null입니다. */
export function useAuth() {
  return useSyncExternalStore(subscribe, getSession, () => null)
}

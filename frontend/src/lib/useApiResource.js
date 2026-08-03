import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * API 조회용 공용 훅입니다.
 * 백엔드가 아직 준비되지 않아 503/500이 흔하므로, 오류를 던지지 않고 상태로 돌려주어
 * 화면이 깨지지 않고 안내 문구를 노출하도록 합니다.
 *
 * @param {Function} loader   () => Promise<any>
 * @param {Array}    deps     의존성 배열 (값이 바뀌면 재조회)
 * @param {object}   options  { enabled: boolean }
 */
export function useApiResource(loader, deps = [], { enabled = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(enabled)
  const [reloadKey, setReloadKey] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return undefined
    }

    let alive = true
    setLoading(true)
    setError(null)

    Promise.resolve()
      .then(() => loaderRef.current())
      .then((result) => {
        if (!alive) return
        setData(result)
        setError(null)
      })
      .catch((err) => {
        if (!alive) return
        setData(null)
        setError(err)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })

    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, reloadKey, ...deps])

  const reload = useCallback(() => setReloadKey((key) => key + 1), [])

  return { data, error, loading, reload, setData }
}

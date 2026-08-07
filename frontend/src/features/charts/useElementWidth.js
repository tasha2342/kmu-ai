import { useEffect, useRef, useState } from 'react'

/**
 * 컨테이너의 실제 픽셀 너비를 구독합니다.
 * viewBox 스케일링으로 늘리면 선 두께가 함께 왜곡되므로, 실제 너비로 그립니다.
 */
export function useElementWidth(fallback = 640) {
  const ref = useRef(null)
  const [width, setWidth] = useState(fallback)

  useEffect(() => {
    const element = ref.current
    if (!element) return undefined

    const update = () => setWidth(Math.max(240, Math.floor(element.clientWidth)))
    update()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }

    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return [ref, width]
}

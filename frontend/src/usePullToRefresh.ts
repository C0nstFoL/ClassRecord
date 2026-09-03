import { useEffect, useRef, useState } from 'react'

const THRESHOLD = 80 // 触发刷新的下拉距离（px）
const MAX_PULL = 120 // 下拉最大距离（px）

/**
 * 下拉刷新手势（触摸设备 / App WebView 内生效）：
 * 页面处于顶部时向下拖动超过阈值即整页 reload，用于拉取最新前端资源与数据。
 * 触点位于可滚动子容器（如转写面板）时不触发，避免与内部滚动冲突。
 */
export function usePullToRefresh() {
  const [pull, setPull] = useState(0)
  const startY = useRef<number | null>(null)
  const refreshing = useRef(false)

  useEffect(() => {
    if (!('ontouchstart' in window)) return

    // 触点是否位于可滚动的内部容器中（转写面板、分段列表等）
    const inScrollable = (target: EventTarget | null) => {
      let el = target instanceof Element ? target : null
      while (el && el !== document.body) {
        const style = window.getComputedStyle(el)
        if (/(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight) {
          return true
        }
        el = el.parentElement
      }
      return false
    }

    const onTouchStart = (e: TouchEvent) => {
      if (refreshing.current) return
      if (window.scrollY > 0 || inScrollable(e.target)) return
      startY.current = e.touches[0].clientY
    }

    const onTouchMove = (e: TouchEvent) => {
      if (startY.current === null) return
      const delta = e.touches[0].clientY - startY.current
      if (delta <= 0) {
        setPull(0)
        return
      }
      // 阻尼：越拉越费劲，接近原生手感
      setPull(Math.min(MAX_PULL, delta * 0.55))
      if (window.scrollY > 0) startY.current = null
    }

    const onTouchEnd = () => {
      if (startY.current !== null && pull >= THRESHOLD) {
        refreshing.current = true
        window.location.reload()
      }
      startY.current = null
      setPull(0)
    }

    window.addEventListener('touchstart', onTouchStart, { passive: true })
    window.addEventListener('touchmove', onTouchMove, { passive: true })
    window.addEventListener('touchend', onTouchEnd)
    return () => {
      window.removeEventListener('touchstart', onTouchStart)
      window.removeEventListener('touchmove', onTouchMove)
      window.removeEventListener('touchend', onTouchEnd)
    }
  }, [pull])

  return pull
}

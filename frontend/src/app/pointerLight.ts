import { useEffect } from 'react'
import { useApp } from './context'
import { usesMaterial } from './theme'

/** 顶栏「光随鼠标」掠光：一帧最多写一次 CSS 变量（绝不做 setState），
 *  由 motion.css 的 .topbar::after 消费 --mx/--my。
 *  仅在材质主题（light/dark）启用；减弱动效或非精细指针环境不挂监听。 */
export function useGlassPointerLight() {
  const { theme } = useApp()
  const enabled = usesMaterial(theme)
  useEffect(() => {
    if (!enabled) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    if (!window.matchMedia('(pointer: fine)').matches) return
    let raf = 0
    let x = 0.5
    let y = 0.35
    const style = document.documentElement.style
    const flush = () => {
      raf = 0
      style.setProperty('--mx', `${(x * 100).toFixed(2)}%`)
      style.setProperty('--my', `${(y * 100).toFixed(2)}%`)
    }
    const onMove = (event: PointerEvent) => {
      x = event.clientX / window.innerWidth
      y = event.clientY / window.innerHeight
      if (!raf) raf = requestAnimationFrame(flush)
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    flush()
    return () => {
      window.removeEventListener('pointermove', onMove)
      if (raf) cancelAnimationFrame(raf)
      style.removeProperty('--mx')
      style.removeProperty('--my')
    }
  }, [enabled])
}

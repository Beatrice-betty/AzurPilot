import { useLayoutEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'

export type NavDirection = 'forward' | 'back' | 'fade'

/* 侧栏顶层导航的展示顺序：同深度页面之间用它判断横向方向（向右 = forward）。 */
const PRIMARY_NAV_ORDER = ['/updater', '/interface', '/remote', '/configs', '/settings', '/dev']
/* 实例内一级页的展示顺序。 */
const INSTANCE_PAGE_ORDER = ['/overview', '/statistics']

export function routeDepth(pathname: string): number {
  return pathname.split('/').filter(Boolean).length
}

function orderIndex(pathname: string, order: readonly string[]): number {
  if (order === INSTANCE_PAGE_ORDER) return order.findIndex(item => pathname.endsWith(item))
  return order.indexOf(pathname)
}

/** 依据「深度变化 → 纵向层级；同深度 → 导航顺序」推断转场方向。
 *  深度增加 = forward（自右滑入）；深度减少 = back（自左滑入）；
 *  无法判定时 fade（如切换任务参数、切换实例）。 */
export function routeDirection(from: string, to: string): NavDirection {
  const depthFrom = routeDepth(from)
  const depthTo = routeDepth(to)
  if (depthTo > depthFrom) return 'forward'
  if (depthTo < depthFrom) return 'back'
  const primaryFrom = orderIndex(from, PRIMARY_NAV_ORDER)
  const primaryTo = orderIndex(to, PRIMARY_NAV_ORDER)
  if (primaryFrom >= 0 && primaryTo >= 0 && primaryFrom !== primaryTo) return primaryTo > primaryFrom ? 'forward' : 'back'
  const instanceFrom = orderIndex(from, INSTANCE_PAGE_ORDER)
  const instanceTo = orderIndex(to, INSTANCE_PAGE_ORDER)
  if (instanceFrom >= 0 && instanceTo >= 0 && instanceFrom !== instanceTo) return instanceTo > instanceFrom ? 'forward' : 'back'
  return 'fade'
}

const DIRECTION_CLASS: Record<NavDirection, string> = {
  forward: 'motion-nav-forward',
  back: 'motion-nav-back',
  fade: 'motion-nav-fade',
}

/** 给 #main-content 挂页面转场类（motion.css 中消费）。
 *  - 首屏挂载与同路径不播；
 *  - 减弱动效环境直接跳过（与全站 reduced-motion 守卫一致）；
 *  - 用 useLayoutEffect 在绘制前挂类，避免「先渲染终态再跳回起点」的闪帧。 */
export function usePageMotion() {
  const location = useLocation()
  const previous = useRef<string | null>(null)
  const cleanupTimer = useRef<number | null>(null)
  useLayoutEffect(() => {
    const pathname = location.pathname
    const target = document.getElementById('main-content')
    const before = previous.current
    previous.current = pathname
    if (!target || before === null || before === pathname) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const className = DIRECTION_CLASS[routeDirection(before, pathname)]
    target.classList.remove('motion-nav-forward', 'motion-nav-back', 'motion-nav-fade')
    // 强制重排：快速连续导航时也要重新触发同一动画。
    void target.offsetWidth
    target.classList.add(className)
    if (cleanupTimer.current !== null) window.clearTimeout(cleanupTimer.current)
    cleanupTimer.current = window.setTimeout(() => {
      target.classList.remove(className)
      cleanupTimer.current = null
    }, 480)
  }, [location.pathname])
}

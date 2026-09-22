import { describe, expect, it } from 'vitest'
import { routeDepth, routeDirection } from './pageMotion'

describe('routeDepth', () => {
  it('按路径段数计算深度', () => {
    expect(routeDepth('/')).toBe(0)
    expect(routeDepth('/settings')).toBe(1)
    expect(routeDepth('/i/demo-main/overview')).toBe(3)
    expect(routeDepth('/i/demo-main/task/Alas')).toBe(4)
  })
})

describe('routeDirection', () => {
  it('深入实例为 forward，返回为 back', () => {
    expect(routeDirection('/', '/i/demo-main/overview')).toBe('forward')
    expect(routeDirection('/i/demo-main/overview', '/')).toBe('back')
    expect(routeDirection('/interface', '/i/demo-main/statistics')).toBe('forward')
  })

  it('顶层导航按侧栏顺序判断横向方向', () => {
    expect(routeDirection('/interface', '/settings')).toBe('forward')
    expect(routeDirection('/settings', '/interface')).toBe('back')
    expect(routeDirection('/updater', '/dev')).toBe('forward')
  })

  it('实例内一级页按 总览→统计 的方向', () => {
    expect(routeDirection('/i/a/overview', '/i/a/statistics')).toBe('forward')
    expect(routeDirection('/i/a/statistics', '/i/a/overview')).toBe('back')
  })

  it('无法判定方向时用淡入（任务参数切换、切换实例）', () => {
    expect(routeDirection('/i/a/task/Alas', '/i/a/task/Daily')).toBe('fade')
    expect(routeDirection('/i/a/overview', '/i/b/overview')).toBe('fade')
    expect(routeDirection('/settings', '/updater')).toBe('back')
  })
})

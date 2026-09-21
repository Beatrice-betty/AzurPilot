import { describe, expect, it } from 'vitest'
import { easeOutCubic, scrollTargetTop } from './scroll'

describe('easeOutCubic', () => {
  it('端点精确、前段快后段慢', () => {
    expect(easeOutCubic(0)).toBe(0)
    expect(easeOutCubic(1)).toBe(1)
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5)
    expect(easeOutCubic(0.5)).toBeCloseTo(0.875, 3)
  })
})

describe('scrollTargetTop', () => {
  it('含 scroll-margin-top', () => {
    expect(scrollTargetTop(0, 0, 500, 85, 2000)).toBe(415)
  })
  it('夹取上下边界', () => {
    expect(scrollTargetTop(0, 0, 50, 85, 2000)).toBe(0)
    expect(scrollTargetTop(0, 0, 5000, 0, 2000)).toBe(2000)
    expect(scrollTargetTop(1200, 0, 300, 0, 2000)).toBe(1500)
  })
  it('容器不可滚动时归零', () => {
    expect(scrollTargetTop(0, 0, 500, 0, -50)).toBe(0)
  })
})

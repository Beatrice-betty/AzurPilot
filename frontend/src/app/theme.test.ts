import { afterEach, describe, expect, it, vi } from 'vitest'
import { readThemePreference, usesMaterial, type Theme } from './theme'

afterEach(() => vi.unstubAllGlobals())

const ALL_THEMES: Theme[] = ['light', 'dark', 'minimal', 'legacy-light', 'legacy-dark']

describe('主题偏好恢复', () => {
  it('保留旧版浅深色偏好，并为缺少的配色提供默认值', () => {
    vi.stubGlobal('localStorage', {getItem: (key: string) => key === 'azurpilot.theme' ? 'dark' : null})
    expect(readThemePreference()).toMatchObject({theme: 'dark', palette: 'ocean'})
  })
  it('恢复简约配色并拒绝未知值', () => {
    vi.stubGlobal('localStorage', {getItem: (key: string) => key === 'azurpilot.theme' ? 'minimal' : 'forest'})
    expect(readThemePreference()).toMatchObject({theme: 'minimal', palette: 'forest'})
    vi.stubGlobal('localStorage', {getItem: () => 'unknown'})
    expect(readThemePreference()).toMatchObject({theme: 'light', palette: 'ocean'})
  })
  it('浏览器禁止存储时仍能启动', () => {
    vi.stubGlobal('localStorage', {getItem: () => {throw new Error('存储不可用')}})
    expect(readThemePreference()).toMatchObject({theme: 'light', palette: 'ocean'})
  })
  it('恢复自动模式与完整自定义方案，失效的方案选择回退到预设', () => {
    const custom = {id: 'custom:one', primary: '#123456', secondary: '#654321'}
    const saved: Record<string, string> = {'azurpilot.theme': 'minimal', 'azurpilot.palette': 'custom:one', 'azurpilot.color-mode': 'dark', 'azurpilot.custom-palettes': JSON.stringify([custom])}
    vi.stubGlobal('localStorage', {getItem: (key: string) => saved[key] ?? null})
    expect(readThemePreference()).toEqual({theme: 'minimal', palette: 'custom:one', colorMode: 'dark', customPalettes: [custom]})
    saved['azurpilot.custom-palettes'] = '{损坏的数据'
    saved['azurpilot.color-mode'] = 'invalid'
    expect(readThemePreference()).toEqual({theme: 'minimal', palette: 'ocean', colorMode: 'auto', customPalettes: []})
  })
})

// 白名单与装饰层判定共同决定毛玻璃、壁纸、标题遮罩与图表取色，改动主题集合时这两处必须同步。
describe('旧版主题注册与装饰层判定', () => {
  it('五个主题都能从存储里恢复', () => {
    for (const theme of ALL_THEMES) {
      vi.stubGlobal('localStorage', {getItem: (key: string) => key === 'azurpilot.theme' ? theme : null})
      expect(readThemePreference().theme).toBe(theme)
    }
    vi.unstubAllGlobals()
  })
  it('只有 Apple 玻璃系主题使用材质装饰', () => {
    const material = ALL_THEMES.filter(usesMaterial)
    expect(material).toEqual(['light', 'dark'])
  })
})

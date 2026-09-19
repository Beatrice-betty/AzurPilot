import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AppContext, type AppContextValue } from '../app/context'
import { Settings } from './Settings'
import { translateUi } from '../i18n'

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react')>()
  return {
    ...actual,
    useSyncExternalStore: (_subscribe: any, getSnapshot: any, getServerSnapshot?: any) => {
      return (getServerSnapshot ?? getSnapshot)()
    },
  }
})

vi.mock('../api/client', () => ({
  api: {
    subscribe: () => () => {},
    getSnapshot: () => 'disconnected',
    request: vi.fn().mockResolvedValue({ groups: [], notice: '', demo: false }),
  },
}))

function createMockContext(theme: AppContextValue['theme']): AppContextValue {
  return {
    instancesLoaded: true,
    instances: [],
    refresh: async () => {},
    t: (key: string) => key,
    ui: (key, params) => translateUi('zh-CN', key, params),
    notify: () => {},
    previewEnabled: false,
    setPreviewEnabled: () => {},
    devMode: false,
    setDevMode: () => {},
    theme,
    setTheme: () => {},
    palette: 'ocean',
    setPalette: () => {},
    colorMode: 'auto',
    resolvedMode: 'light',
    setColorMode: () => {},
    customPalettes: [],
    saveCustomPalette: () => {},
    deleteCustomPalette: () => {},
    language: 'zh-CN',
    setLanguage: () => {},
  }
}

describe('Settings 页面自定义背景显示逻辑', () => {
  it('浅色主题下渲染自定义背景，不渲染简约配色方案', () => {
    const html = renderToStaticMarkup(
      <AppContext.Provider value={createMockContext('light')}>
        <Settings />
      </AppContext.Provider>
    )
    expect(html).toContain('自定义背景')
    expect(html).not.toContain('配色方案')
  })

  it('深色主题下渲染自定义背景，不渲染简约配色方案', () => {
    const html = renderToStaticMarkup(
      <AppContext.Provider value={createMockContext('dark')}>
        <Settings />
      </AppContext.Provider>
    )
    expect(html).toContain('自定义背景')
    expect(html).not.toContain('配色方案')
  })

  it('简约主题下不渲染自定义背景，仅渲染简约配色方案', () => {
    const html = renderToStaticMarkup(
      <AppContext.Provider value={createMockContext('minimal')}>
        <Settings />
      </AppContext.Provider>
    )
    expect(html).not.toContain('自定义背景')
    expect(html).toContain('配色方案')
  })
})

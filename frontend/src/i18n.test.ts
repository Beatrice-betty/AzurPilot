import { describe, expect, it } from 'vitest'
import { detectLanguage, translateUi } from './i18n'

describe('WebUI i18n', () => {
  it('detects supported browser locales', () => {
    expect(detectLanguage(['zh-HK'])).toBe('zh-TW')
    expect(detectLanguage(['ja'])).toBe('ja-JP')
    expect(detectLanguage(['en-GB'])).toBe('en-US')
    expect(detectLanguage(['fr-FR'])).toBe('zh-CN')
  })

  it('interpolates translated values', () => {
    expect(translateUi('en-US', 'instance.deletePrompt', {name: 'alas-main'})).toBe('Delete alas-main? Its configuration will remain in backup.')
  })

  it('keeps the Miao locale complete through its Simplified Chinese base', () => {
    expect(translateUi('zh-MIAO', 'nav.statistics')).toBe('资源统计')
  })

  it('keeps Japanese and Traditional Chinese dictionaries complete for formerly missing UI keys', () => {
    expect(translateUi('ja-JP', 'log.search')).toBe('ログを検索')
    expect(translateUi('ja-JP', 'resource.Oil')).toBe('燃料')
    expect(translateUi('zh-TW', 'fleet.vanguard')).toBe('先鋒艦隊')
    expect(translateUi('zh-TW', 'stats.toolboxSave')).toBe('儲存圖表')
  })

  it('translates developer playground UI instead of leaving hardcoded labels', () => {
    expect(translateUi('en-US', 'developer.pageTitle')).toBe('Developer · Control Preview')
    expect(translateUi('ja-JP', 'developer.formControls')).toBe('フォームコントロール')
    expect(translateUi('zh-TW', 'developer.emptyTitle')).toBe('暫無內容')
  })
})

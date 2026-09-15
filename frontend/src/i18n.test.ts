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

  it('falls back to Simplified Chinese when a locale omits a key', () => {
    expect(translateUi('zh-MIAO', 'nav.statistics')).toBe('资源统计')
  })
})

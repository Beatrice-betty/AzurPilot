import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AppContext, type AppContextValue } from '../app/context'
import { translateUi } from '../i18n'
import type { MeowfficerScoreReport } from '../api/types'
import { MeowfficerScoreList, splitHit, tierClass } from './MeowfficerScorePanel'

const context = {ui: (key: Parameters<AppContextValue['ui']>[0], params?: Parameters<AppContextValue['ui']>[1]) => translateUi('zh-CN', key, params)} as AppContextValue
const render = (report: MeowfficerScoreReport) => renderToStaticMarkup(
  <AppContext.Provider value={context}><MeowfficerScoreList report={report}/></AppContext.Provider>,
)

const report: MeowfficerScoreReport = {
  instance: 'test', generatedAt: '2026-09-20 11:44:26', count: 1,
  cats: [{
    source: 'shot_0.png', cat: '克雷喵', tags: ['SSR', '铁血'], fixed: true, maxed: true, pointsSpent: 6,
    note: '<b>初始池小</b>、最好毕业',
    talents: [
      {name: '狼群之首', level: 1, kind: 'special'},
      {name: '装填新手·潜艇', level: 3, kind: 'normal', inferred: true},
    ],
    rubrics: [
      {key: 'submarine', label: '潜艇猫', tier: '准毕业', score: 100, x: 1, y: 6,
        xHits: ['狼群之首 Lv1'], yHits: ['雷击长·潜艇 Lv3'], notes: ['缺 侵略如火'],
        source: '28法则执行篇·潜艇猫', primary: true},
      {key: 'low_cost', label: '低耗猫', tier: '不适合低耗', score: 35, x: 0, y: 2, xHits: [], yHits: [], primary: false},
    ],
  }],
}

describe('指挥喵评分面板', () => {
  it('按档位关键词上色，准毕业不会被毕业级抢先匹配', () => {
    expect(tierClass('准毕业')).toBe('is-near')
    expect(tierClass('毕业级')).toBe('is-graduate')
    expect(tierClass('零食（建议喂掉）')).toBe('is-snack')
    expect(tierClass('没见过的档位')).toBe('is-other')
  })

  it('把命中标签里的等级拆成徽章', () => {
    expect(splitHit('雷击长·潜艇 Lv3')).toEqual({name: '雷击长·潜艇', level: 3})
    expect(splitHit('狼群之首')).toEqual({name: '狼群之首', level: 0})
  })

  it('展示主口径的档位、参考分、命中标签与说明', () => {
    const html = render(report)
    expect(html).toContain('克雷喵')
    expect(html).toContain('准毕业')
    expect(html).toContain('x + y = 1 + 6.0')
    expect(html).toContain('雷击长·潜艇')
    expect(html).toContain('Ⅲ')
    expect(html).toContain('缺 侵略如火')
    expect(html).toContain('依据：28法则执行篇·潜艇猫')
    expect(html).toContain('已满级')
    expect(html).toContain('已指定')
    expect(html).toContain('来源截图：shot_0.png')
  })

  it('彩天赋高亮、推断天赋标注，其余口径折叠展示', () => {
    const html = render(report)
    expect(html).toContain('meow-talent is-special')
    expect(html).toContain('meow-talent is-inferred')
    expect(html).toContain('推断条目，请核对')
    expect(html).toContain('<details class="meow-others">')
    expect(html).toContain('其他口径（1）')
    expect(html).toContain('不适合低耗')
  })

  it('后端文案按纯文本渲染，不解析其中的标签', () => {
    const html = render(report)
    expect(html).toContain('&lt;b&gt;初始池小&lt;/b&gt;')
    expect(html).not.toContain('<b>')
  })
})

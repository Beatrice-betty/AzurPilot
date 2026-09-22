import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AppContext, type AppContextValue } from '../app/context'
import type { Resource } from '../api/types'
import { translateUi } from '../i18n'
import { moveResourceKey, ResourceCards } from './ResourceCards'

function renderResources(resources: Resource[], selected = ['ActionPoint']) {
  return renderToStaticMarkup(
    <AppContext.Provider value={{ui: (key, params) => translateUi('zh-CN', key, params)} as AppContextValue}>
      <ResourceCards resources={resources} selected={selected}/>
    </AppContext.Provider>,
  )
}

describe('资源卡片', () => {
  it('按拖动目标重排卡片且不修改原数组', () => {
    const original = ['Oil', 'Coin', 'Gem', 'Cube']

    expect(moveResourceKey(original, 'Cube', 'Coin')).toEqual(['Oil', 'Cube', 'Coin', 'Gem'])
    expect(moveResourceKey(original, 'Oil', 'Cube')).toEqual(['Coin', 'Gem', 'Cube', 'Oil'])
    expect(moveResourceKey(original, 'Oil', 'Oil')).toBe(original)
    expect(original).toEqual(['Oil', 'Coin', 'Gem', 'Cube'])
  })

  it('行动力与石油一致，以小字后缀显示总量', () => {
    const html = renderResources([{name: 'ActionPoint', label: '行动力', value: 101, total: 1301, record: '2026-09-16 12:00:00'}])

    expect(html).toContain('<span>行动力</span>')
    expect(html).toContain('<span>101</span><small>/ 1,301</small>')
  })

  it.each([101, 0])('总行动力与当前行动力相等时仍显示完整数值：%s', value => {
    const html = renderResources([{name: 'ActionPoint', label: '行动力', value, total: value, record: '2026-09-16 12:00:00'}])

    expect(html).toContain(`<span>${value}</span><small>/ ${value}</small>`)
  })

  it.each([undefined, 0, 100, NaN, Infinity])('总行动力缺失或异常时回退到当前行动力：%s', total => {
    const html = renderResources([{name: 'ActionPoint', label: '行动力', value: 101, total, record: '2026-09-16 12:00:00'}])

    expect(html).toContain('<span>行动力</span>')
    expect(html).toContain('<span>101</span>')
    expect(html).not.toContain('总行动力')
  })

  it.each([undefined, '2020-01-01 00:00:00'])('尚未同步时不展示总量和明细：%s', record => {
    const html = renderResources([{name: 'ActionPoint', label: '行动力', value: 101, total: 1301, record}])

    expect(html).toContain('<span>—</span>')
    expect(html).not.toContain('1,301')
    expect(html).not.toContain('<small>')
  })

  it('其他资源仍显示当前值和上限', () => {
    const html = renderResources([{name: 'Oil', label: '石油', value: 1000, limit: 16000, record: '2026-09-16 12:00:00'}], ['Oil'])

    expect(html).toContain('<span>1,000</span>')
    expect(html).toContain('<small>/ 16,000</small>')
  })
})

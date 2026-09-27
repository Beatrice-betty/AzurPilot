import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  aggregateEntriesToCards,
  MapGridCard,
  PerspectiveCard,
  PropertySheetCard,
  DataTableCard,
  ErrorContextCard,
} from './LogCardView'

describe('LogCardView 块级聚合器与卡片组件', () => {
  it('正确将三行式 hr(0) 规则聚合成单个系统横幅卡片', () => {
    const entries = [
      { id: 1, level: 'INFO', text: '═'.repeat(60) },
      { id: 2, level: 'INFO', text: ' '.repeat(20) + '启动' + ' '.repeat(20) },
      { id: 3, level: 'INFO', text: '═'.repeat(60) },
    ]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('system_banner')
    if (cards[0].type === 'system_banner') {
      expect(cards[0].title).toBe('启动')
    }
  })

  it('正确消除 Level 1/2 HR 下方重复出现的同名 INFO 标题', () => {
    const entries = [
      { id: 1, level: 'INFO', text: '═'.repeat(20) + ' COMMISSION ' + '═'.repeat(20) },
      { id: 2, level: 'INFO', text: 'INFO     14:24:30.120 │ COMMISSION' },
    ]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('stage_header')
    if (cards[0].type === 'stage_header') {
      expect(cards[0].title).toBe('COMMISSION')
      expect(cards[0].level).toBe(1)
    }
  })

  it('正确聚合海域透视与边缘线识别（两行紧密拓扑 / _ \\）', () => {
    const entries = [
      { id: 1, level: 'INFO', text: 'INFO 14:24:30.500 │ [地图-透视] 0.045s  _   水平: 7 (7 内部, 0 边缘)' },
      { id: 2, level: 'INFO', text: 'INFO 14:24:30.501 │ [地图-透视] 边缘: /_\\    垂直: 8 (8 内部, 0 边缘)' },
    ]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('perspective')
    if (cards[0].type === 'perspective') {
      expect(cards[0].duration).toBe('0.045s')
      expect(cards[0].lowerEdge).toBe(true)
      expect(cards[0].leftEdge).toBe(true)
      expect(cards[0].upperEdge).toBe(true)
      expect(cards[0].rightEdge).toBe(true)

      const html = renderToStaticMarkup(<PerspectiveCard card={cards[0]} />)
      expect(html).toContain('perspective-card')
      expect(html).toContain('trapezoid-visual')
      expect(html).toContain('0.045s')
      expect(html).toContain('左边缘 ✓ 可见')
      expect(html).toContain('4 边完整闭合')
    }
  })

  it('正确表现缺失边界时的红色虚线与状态标记', () => {
    // 右边缘与下边缘缺失
    const entries = [
      { id: 1, level: 'INFO', text: 'INFO 14:24:30.500 │ [地图-透视] 0.041s      水平: 5 (5 内部, 0 边缘)' },
      { id: 2, level: 'INFO', text: 'INFO 14:24:30.501 │ [地图-透视] 边缘: /_     垂直: 6 (6 内部, 0 边缘)' },
    ]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('perspective')
    if (cards[0].type === 'perspective') {
      expect(cards[0].lowerEdge).toBe(false)
      expect(cards[0].rightEdge).toBe(false)
      expect(cards[0].leftEdge).toBe(true)
      expect(cards[0].upperEdge).toBe(true)

      const html = renderToStaticMarkup(<PerspectiveCard card={cards[0]} />)
      expect(html).toContain('edge-missing')
      expect(html).toContain('缺失 2 边')
      expect(html).toContain('右(缺失)')
      expect(html).toContain('下(缺失)')
      expect(html).toContain('右边缘 ✗ 缺失')
      expect(html).toContain('下边缘 ✗ 缺失')
    }
  })

  it('正确将 [地图-显示] 与连续数据行聚合成单个海图战术卡片', () => {
    const entries = [
      { id: 1, level: 'INFO', text: 'INFO 14:24:31.851 │ [地图-显示]   A  B  C  D  E  F  G  H' },
      { id: 2, level: 'INFO', text: 'INFO 14:24:31.852 │  1 ++ ++ ++ -- -- -- -- --' },
      { id: 3, level: 'INFO', text: 'INFO 14:24:31.853 │  2 ++ ++ ++ -- 1M -- -- --' },
      { id: 4, level: 'INFO', text: 'INFO 14:24:31.854 │  3 -- -- FL -- -- -- 2C BO' },
    ]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('map_grid')
    if (cards[0].type === 'map_grid') {
      expect(cards[0].cols).toEqual(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'])
      expect(cards[0].rows).toHaveLength(3)
      expect(cards[0].rows[0].cells).toEqual(['++', '++', '++', '--', '--', '--', '--', '--'])

      const html = renderToStaticMarkup(<MapGridCard card={cards[0]} />)
      expect(html).toContain('map-card')
      expect(html).toContain('海域战术地图快照')
      expect(html).toContain('8×3')
      expect(html).toContain('cell-fleet-1')
      expect(html).toContain('cell-boss')
      expect(html).toContain('cell-enemy')
      expect(html).toContain('cell-land')
    }
  })

  it('正确将连续 attr_align 聚合成属性清单卡片', () => {
    const entries = [
      { id: 1, level: 'INFO', text: 'INFO 14:24:32.410 │                    摄像机: (4, 3)' },
      { id: 2, level: 'INFO', text: 'INFO 14:24:32.411 │                  摄像机修正: (4, 3) -> (5, 3)' },
      { id: 3, level: 'INFO', text: 'INFO 14:24:32.412 │                 之前中心偏移: (12, -4)' },
    ]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('property_sheet')
    if (cards[0].type === 'property_sheet') {
      expect(cards[0].items).toHaveLength(3)
      expect(cards[0].items[0]).toEqual({ key: '摄像机', value: '(4, 3)' })

      const html = renderToStaticMarkup(<PropertySheetCard card={cards[0]} search="" />)
      expect(html).toContain('property-card')
      expect(html).toContain('摄像机')
      expect(html).toContain('4, 3')
      expect(html).toContain('hl-brace')
    }
  })

  it('正确将 Unicode Rich 表格解析为原生数据表格卡片', () => {
    const rawTable = [
      '                                Benchmark Result                                ',
      '                  ┌──────────────┬──────────┬──────┬─────────┐                  ',
      '                  │ Device       │  Method  │  FPS │ Latency │                  ',
      '                  ├──────────────┼──────────┼──────┼─────────┤                  ',
      '                  │ MuMuPlayer12 │ nemu_ipc │ 58.4 │  0.005s │                  ',
      '                  └──────────────┴──────────┴──────┴─────────┘                  ',
    ].join('\n')
    const entries = [{ id: 1, level: 'INFO', text: rawTable }]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('data_table')
    if (cards[0].type === 'data_table') {
      expect(cards[0].headers).toEqual(['Device', 'Method', 'FPS', 'Latency'])
      expect(cards[0].rows[0]).toEqual(['MuMuPlayer12', 'nemu_ipc', '58.4', '0.005s'])

      const html = renderToStaticMarkup(<DataTableCard card={cards[0]} />)
      expect(html).toContain('table-card')
      expect(html).toContain('native-log-table')
      expect(html).toContain('MuMuPlayer12')
      expect(html).toContain('nemu_ipc')
    }
  })

  it('正确将四段式 error_context 渲染为警示操作卡片', () => {
    const rawError = [
      '[错误] 游戏状态无法推进',
      '原因：无操作超时。',
      '影响：任务中断。',
      '建议：检查模拟器。',
      '异常：GameStuckError',
    ].join('\n')
    const entries = [{ id: 1, level: 'ERROR', text: rawError }]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('error_context')
    if (cards[0].type === 'error_context') {
      expect(cards[0].title).toBe('游戏状态无法推进')
      expect(cards[0].reason).toBe('无操作超时。')
      expect(cards[0].action).toBe('检查模拟器。')

      const html = renderToStaticMarkup(<ErrorContextCard card={cards[0]} />)
      expect(html).toContain('error-card')
      expect(html).toContain('建议操作')
      expect(html).toContain('检查模拟器。')
      expect(html).toContain('复制错误现场')
    }
  })
})

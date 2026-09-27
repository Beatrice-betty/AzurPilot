import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  aggregateEntriesToCards,
  MapGridCard,
  PerspectiveCard,
  PropertySheetCard,
  DataTableCard,
  ErrorContextCard,
  renderCellContent,
  CostGridCard,
  getCostHeatmapStyle,
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
      expect(html).toContain('edge-active')
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
      expect(html).toContain('0.041s')
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

  it('正确解析并渲染带 +--+ 边界的经典 ASCII 表格与状态颜色', () => {
    const rawAsciiTable = [
      '                                Legacy ASCII Benchmark                          ',
      '                      +--------------+--------+--------+                        ',
      '                      |  Screenshot  |  Time  | Speed  |                        ',
      '                      +--------------+--------+--------+                        ',
      '                      |     ADB      | 0.319s |  Fast  |                        ',
      '                      | uiautomator2 | 0.476s | Medium |                        ',
      '                      |  aScreenCap  | Failed | Failed |                        ',
      '                      +--------------+--------+--------+                        ',
    ].join('\n')
    const entries = [{ id: 1, level: 'INFO', text: rawAsciiTable }]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('data_table')
    if (cards[0].type === 'data_table') {
      expect(cards[0].title).toBe('Legacy ASCII Benchmark')
      expect(cards[0].headers).toEqual(['Screenshot', 'Time', 'Speed'])
      expect(cards[0].rows).toHaveLength(3)

      const html = renderToStaticMarkup(<DataTableCard card={cards[0]} />)
      expect(html).toContain('Legacy ASCII Benchmark')
      expect(html).toContain('0.319s')
      expect(html).toContain('Medium')
      expect(html).toContain('Failed')
    }
  })

  it('正确将四段式 error_context 渲染为警示操作卡片并完整直接渲染堆栈', () => {
    const rawError = [
      '[错误] 任务执行发生未处理异常（opsi_ash_beacon）',
      '原因：程序抛出了 ScriptEnd。',
      '影响：当前任务中断。',
      '建议：查看完整堆栈。',
      '异常：ScriptEnd: 计算模式红脸弹窗',
      '╭ Traceback (most recent call last) ╮',
      '│ E:\\AzurPilot\\alas.py:1019 in run │',
      '│ ❱ 1019 │ self.__getattribute__(command)() │',
      '│ ╭ locals ╮ │',
      '│ │ command = \'opsi_ash_beacon\' │ │',
      '│ ╰────────╯ │',
      '╰───────────────────────────────────╯',
      'ScriptEnd: 计算模式红脸弹窗',
    ].join('\n')
    const entries = [{ id: 1, level: 'ERROR', text: rawError }]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('error_context')
    if (cards[0].type === 'error_context') {
      expect(cards[0].title).toBe('任务执行发生未处理异常（opsi_ash_beacon）')
      expect(cards[0].reason).toBe('程序抛出了 ScriptEnd。')
      expect(cards[0].action).toBe('查看完整堆栈。')
      expect(cards[0].stackTrace).toContain('Traceback')

      const html = renderToStaticMarkup(<ErrorContextCard card={cards[0]} />)
      expect(html).toContain('error-card')
      expect(html).toContain('建议操作')
      expect(html).toContain('查看完整堆栈。')
      expect(html).toContain('traceback-viewer')
      expect(html).toContain('alas.py')
      expect(html).toContain('1019')
      expect(html).toContain('fault-row')
      expect(html).toContain('opsi_ash_beacon')
      expect(html).toContain('ScriptEnd: 计算模式红脸弹窗')
    }
  })

  it('海域方格保持纯净无冗余波浪图标，大世界战术地标全量覆盖图标', () => {
    // 海域航道不渲染图标，返回 null
    expect(renderCellContent('--')).toBeNull()
    expect(renderCellContent('==')).toBeNull()

    // 大世界战术图标全面覆盖
    const meHtml = renderToStaticMarkup(<>{renderCellContent('ME')}</>)
    expect(meHtml).toContain('大世界指挥喵搜索点')

    const exHtml = renderToStaticMarkup(<>{renderCellContent('EX')}</>)
    expect(exHtml).toContain('大世界感叹号特殊事件')

    const sdHtml = renderToStaticMarkup(<>{renderCellContent('SD')}</>)
    expect(sdHtml).toContain('大世界环境扫描探测装置')

    const arHtml = renderToStaticMarkup(<>{renderCellContent('AR')}</>)
    expect(arHtml).toContain('大世界机密档案记录')

    const poHtml = renderToStaticMarkup(<>{renderCellContent('PO')}</>)
    expect(poHtml).toContain('大世界补给港口')

    const enHtml = renderToStaticMarkup(<>{renderCellContent('EN')}</>)
    expect(enHtml).toContain('敌舰 (EN)')

    const boHtml = renderToStaticMarkup(<>{renderCellContent('BO')}</>)
    expect(boHtml).toContain('关卡旗舰 Boss')
  })

  it('正确渲染寻路移动代价热力图 (CostGridCard)：9999为纯黑色底，0为绿色起点，其余按代价渐变', () => {
    // 单元样式断言
    const wallStyle = getCostHeatmapStyle(9999, 8)
    expect(wallStyle.backgroundColor).toBe('#000000')

    const originStyle = getCostHeatmapStyle(0, 8)
    expect(originStyle.backgroundColor).toBe('#10b981')
    expect(originStyle.fontWeight).toBe(700)

    const minStyle = getCostHeatmapStyle(1, 8)
    expect(minStyle.backgroundColor).toBe('rgb(2, 132, 199)')

    const maxStyle = getCostHeatmapStyle(8, 8)
    expect(maxStyle.backgroundColor).toBe('rgb(225, 29, 72)')

    // 聚合与卡片渲染断言
    const entries = [
      { id: 1, level: 'INFO', text: 'INFO 14:24:30.100 │       A    B    C' },
      { id: 2, level: 'INFO', text: 'INFO 14:24:30.101 │  1 9999    2    3' },
      { id: 3, level: 'INFO', text: 'INFO 14:24:30.102 │  2    1    0    1' },
    ]
    const cards = aggregateEntriesToCards(entries)
    expect(cards).toHaveLength(1)
    expect(cards[0].type).toBe('cost_grid')

    if (cards[0].type === 'cost_grid') {
      const html = renderToStaticMarkup(<CostGridCard card={cards[0]} />)
      expect(html).toContain('寻路移动代价热力图')
      expect(html).toContain('cost-wall')
      expect(html).toContain('background-color:#000000')
      expect(html).toContain('cost-origin')
      expect(html).toContain('background-color:#10b981')
      expect(html).toContain('不可达障碍 (9999)')
      expect(html).toContain('寻路起点 (0 步)')
    }
  })
})

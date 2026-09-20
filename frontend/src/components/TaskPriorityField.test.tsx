import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AppContext, type AppContextValue } from '../app/context'
import type { Schema } from '../api/types'
import { translateUi } from '../i18n'
import { insertionIndexAt, TaskPriorityField } from './TaskPriorityField'

const schema = {
  args: {
    Alas: {Scheduler: {Command: {type: 'input', value: 'Alas'}}},
    Main: {Scheduler: {Command: {type: 'input', value: 'Main'}}},
    Main2: {Scheduler: {Command: {type: 'input', value: 'Main2'}}},
  },
} as unknown as Schema
const taskNames: Record<string, string> = {'Task.Alas.name': '常驻设置', 'Task.Main.name': '主线图', 'Task.Main2.name': '主线图2'}

function render(value: string, fallback = 'Alas > Main > Main2', disabled = false) {
  const context = {
    ui: (key, params) => translateUi('zh-CN', key, params),
    t: (key: string) => taskNames[key] ?? key,
    schema,
  } as AppContextValue
  return renderToStaticMarkup(
    <AppContext.Provider value={context}>
      <TaskPriorityField id="General.YukikazeTaskManager.TaskPriorityAdjustment" label="任务优先级调整" value={value} fallback={fallback} disabled={disabled} onChange={() => {}}/>
    </AppContext.Provider>,
  )
}

describe('任务优先级拖动列表', () => {
  it('按生效顺序渲染任务行', () => {
    const html = render('Main > Alas')

    expect(html.indexOf('data-task="Main"')).toBeGreaterThan(-1)
    expect(html.indexOf('data-task="Main"')).toBeLessThan(html.indexOf('data-task="Alas"'))
    expect(html).toContain('主线图')
    expect(html).toContain('aria-label="任务优先级调整"')
  })

  it('配置里缺失的任务按默认位置补进列表', () => {
    const html = render('Main2 > Alas')

    expect(html.indexOf('data-task="Main2"')).toBeLessThan(html.indexOf('data-task="Alas"'))
    expect(html.indexOf('data-task="Alas"')).toBeLessThan(html.indexOf('data-task="Main"'))
  })

  it('每行都有上下移动按钮，首行的上移与末行的下移不可用', () => {
    const html = render('Main > Alas')

    expect(html.match(/aria-label="上移 /g)).toHaveLength(3)
    expect(html.match(/aria-label="下移 /g)).toHaveLength(3)
    expect(html.match(/disabled=""/g)).toHaveLength(2)
  })

  it('只读时不给拖动与排序入口', () => {
    const html = render('Main > Alas', 'Alas > Main > Main2', true)

    expect(html).toContain('data-task="Main"')
    expect(html).not.toContain('aria-label="上移')
    expect(html).not.toContain('task-priority-move')
  })

  it('行里带上任务代号，方便与日志和文档对照', () => {
    const html = render('Main > Alas')

    expect(html).toMatch(/task-priority-code[^>]*>Main<\/span>/)
    expect(html).toMatch(/task-priority-code[^>]*>Alas<\/span>/)
  })
})

describe('拖动落点计算', () => {
  const rows = [0, 40, 80, 120].map(top => ({top, height: 40}))

  it('按行的垂直中点决定插到哪一行之前', () => {
    expect(insertionIndexAt(rows, 10, -1)).toBe(0)
    expect(insertionIndexAt(rows, 59, -1)).toBe(1)
    expect(insertionIndexAt(rows, 61, -1)).toBe(2)
  })

  it('被拖的行本身不参与比较', () => {
    expect(insertionIndexAt(rows, 30, 1)).toBe(2)
  })

  it('拖到所有行下方时落在列表末尾', () => {
    expect(insertionIndexAt(rows, 400, 0)).toBe(4)
  })
})

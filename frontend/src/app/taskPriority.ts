/** 任务调度优先级的解析与排序工具。
 *
 * 与 `module/config/task_priority.py` 保持同一套语义：换行与 `>` 都只是分隔符，
 * `#` 之后是注释，全角箭头按半角处理，重复任务只保留首次出现的位置。
 * 界面按同样的规则合并出「实际生效顺序」，拖动保存的才是运行器真正使用的顺序。
 */

import type { Schema } from '../api/types'

/** 写回配置的文本格式，与 Python 的 `PRIORITY_SEPARATOR` 一致。 */
export const PRIORITY_SEPARATOR = '\n> '
const ARROW = /[＞﹥›˃ᐳ❯]/g
const LINE_BREAK = /\r\n|[\n\r\v\f\u2028\u2029]/
const COMMENT = '#'

/** 解析优先级文本，返回去重后的任务名列表。 */
export function parseTaskPriority(value: unknown): string[] {
  if (!value) return []
  const tasks: string[] = []
  const seen = new Set<string>()
  for (const rawLine of String(value).replace(ARROW, '>').split(LINE_BREAK)) {
    const line = rawLine.split(COMMENT)[0].trim()
    if (!line) continue
    for (const rawTask of line.split('>')) {
      const task = rawTask.trim()
      if (!task || seen.has(task)) continue
      seen.add(task)
      tasks.push(task)
    }
  }
  return tasks
}

/** 把任务名列表格式化成配置文件里的优先级文本。 */
export function formatTaskPriority(tasks: readonly string[]): string {
  return tasks.map(task => String(task).trim()).filter(Boolean).join(PRIORITY_SEPARATOR)
}

/** 参与调度的任务清单：带 `Scheduler.Command` 的任务，顺序与参数模板一致。 */
export function schedulerTasks(args: Schema['args'] | undefined): string[] {
  const tasks: string[] = []
  for (const groups of Object.values(args ?? {})) {
    const command = groups?.Scheduler?.Command?.value
    if (typeof command === 'string' && command && !tasks.includes(command)) tasks.push(command)
  }
  return tasks
}

/** 新任务按内置默认优先级插入：先找它前面最近的一个已知任务，插到那之后。 */
function insertByDefaultNeighbors(ordered: string[], task: string, defaultOrder: readonly string[]): void {
  if (ordered.includes(task)) return
  const index = defaultOrder.indexOf(task)
  if (index < 0) {
    ordered.push(task)
    return
  }
  const previous = defaultOrder.slice(0, index).reverse().find(candidate => ordered.includes(candidate))
  const next = defaultOrder.slice(index + 1).find(candidate => ordered.includes(candidate))
  if (previous !== undefined) ordered.splice(ordered.indexOf(previous) + 1, 0, task)
  else if (next !== undefined) ordered.splice(ordered.indexOf(next), 0, task)
  else ordered.push(task)
}

/** 合并用户顺序与内置默认顺序：剔除已不存在的任务，按默认位置补入尚未出现的任务。 */
export function mergeTaskPriority(current: unknown, fallback: unknown, available: readonly string[]): string[] {
  const defaultOrder = parseTaskPriority(fallback)
  const pool = available.length ? [...available] : defaultOrder
  const known = new Set(pool)
  const ordered = parseTaskPriority(current).filter(task => known.has(task))
  const defaultAvailable = defaultOrder.filter(task => known.has(task))
  for (const task of defaultAvailable) insertByDefaultNeighbors(ordered, task, defaultAvailable)
  for (const task of pool) insertByDefaultNeighbors(ordered, task, defaultAvailable)
  return ordered
}

/** 把第 `from` 项移动到第 `to` 项的位置；下标越界时原样返回。 */
export function moveTaskPriority(tasks: readonly string[], from: number, to: number): string[] {
  const next = [...tasks]
  if (from === to || from < 0 || to < 0 || from >= next.length || to >= next.length) return next
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

/** 按「插入到第 `insertIndex` 项之前」的语义排序，拖动落点用的就是这种下标。 */
export function reorderTaskPriority(tasks: readonly string[], from: number, insertIndex: number): string[] {
  return moveTaskPriority(tasks, from, insertIndex > from ? insertIndex - 1 : insertIndex)
}

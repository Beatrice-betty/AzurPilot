import { api } from '../api/client'
import type { Field, Value } from '../api/types'
import { EditQueue } from './EditQueue'

const prefix = 'azurpilot.edits.'
const queues = new Map<string, EditQueue>()

export function editor(scope: string) {
  let queue = queues.get(scope)
  if (queue) return queue
  let storage: Storage | undefined
  try { storage = window.sessionStorage } catch { /* 队列仍在内存中工作，并显示持久化失败。 */ }
  queue = new EditQueue(prefix + scope, {
    ready: () => api.getSnapshot() === 'ready',
    send: (path, value) => scope === 'deploy'
      ? api.request('settings.patch', {values: {[path]: value}})
      : scope.startsWith('startup:')
        ? api.request('startup.set', {instance: scope.slice(8), enabled: value as boolean})
        : api.request('config.patch', {instance: scope.slice(7), changes: [{path, value}]}),
  }, storage)
  queues.set(scope, queue)
  return queue
}

/** 重连和整页刷新后，恢复所有实例的未完成提交，不依赖当前显示哪个页面。 */
export function resumeEditors() {
  try {
    for (const key of Object.keys(window.sessionStorage)) {
      if (key.startsWith(prefix)) editor(key.slice(prefix.length))
    }
  } catch { /* 浏览器禁用存储时仍重试内存队列。 */ }
  for (const queue of queues.values()) queue.retry()
}

/** 数字的原始文本与提交值分离，保留空值、负号、小数点等输入中间态。 */
export function prepareValue(value: Value, field: Pick<Field, 'type' | 'value' | 'validate'>): {payload: Value; error?: string} {
  const numeric = !['select', 'multiselect', 'checkbox'].includes(field.type)
    && (typeof field.value === 'number' || ['number', 'int', 'float'].includes(field.type))
  if (!numeric) return {payload: value}
  const text = String(value).trim()
  const number = Number(text)
  // JSON 无法区分默认值 1 与 1.0；任务字段的精确整数类型交给后端校验。
  const integer = field.type === 'int'
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text) || !Number.isFinite(number)) {
    return {payload: value, error: '请输入完整、有效的数字；输入内容已保留。'}
  }
  if (integer && !Number.isSafeInteger(number)) return {payload: value, error: '请输入有效整数；输入内容已保留。'}
  if (Number.isInteger(number) && !Number.isSafeInteger(number)) return {payload: value, error: '数字超出可精确表示的范围。'}
  if (Array.isArray(field.validate) && (number < field.validate[0] || number > field.validate[1])) {
    return {payload: value, error: `请输入 ${field.validate[0]} 到 ${field.validate[1]} 之间的数值。`}
  }
  return {payload: number}
}

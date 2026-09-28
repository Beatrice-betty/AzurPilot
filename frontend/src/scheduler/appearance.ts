/** 卡片类别和端口类型共用的调色板，连线与来源端口保持同色。 */
import type {PortType} from './types'

export const categoryColors: Record<string, string> = {
  '流程': '#6385d6', '逻辑': '#9970d1', '变量': '#329caa', '时间': '#c58a27',
  '资源': '#289a77', '任务': '#d45982', '列表': '#358dcc', '调度': '#c3733f', '组合': '#7963d3',
}
export const categoryColor = (category: string) => categoryColors[category] ?? '#7b8b9e'
export const controlColor = '#64748b'
export const portFamily = (type: PortType) => ({duration:'number', tasks:'list', resource:'object'} as Partial<Record<PortType,PortType>>)[type] ?? type
export const compatiblePorts = (source: PortType, target: PortType) => source === 'any' || target === 'any' || portFamily(source) === portFamily(target)
export const portColors: Record<PortType, string> = {
  any: '#8191a5', number: '#329caa', boolean: '#9970d1', string: '#63a04a',
  time: '#c58a27', duration: '#329caa', resource: '#289a77', task: '#d45982',
  tasks: '#358dcc', result: '#c3733f', list: '#358dcc', object: '#289a77',
}
export const wildcardBackground = 'conic-gradient(#329caa, #9970d1, #63a04a, #c58a27, #289a77, #d45982, #358dcc, #329caa)'

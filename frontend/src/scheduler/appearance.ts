/** 卡片类别和端口类型共用的调色板与图标映射，连线与来源端口保持同色。 */
import type {LucideIcon} from 'lucide-react'
import {
  AlarmClock,
  ArrowDownToDot,
  ArrowRightFromLine,
  ArrowRightToLine,
  ArrowUpDown,
  ArrowUpWideNarrow,
  Binary,
  BookmarkPlus,
  Box,
  Braces,
  Bug,
  Calculator,
  CalendarClock,
  CalendarDays,
  CalendarRange,
  CheckSquare,
  CircleSlash,
  CircleStop,
  Clock,
  Clock3,
  Coins,
  Compass,
  CornerDownLeft,
  Cpu,
  Filter,
  Gauge,
  GitBranch,
  Hash,
  History,
  Hourglass,
  Layers,
  ListFilter,
  ListTodo,
  PenTool,
  Play,
  Radio,
  RefreshCw,
  Repeat,
  Repeat1,
  Scale,
  Settings2,
  Shuffle,
  Snowflake,
  Sparkles,
  StepForward,
  Sunrise,
  Swords,
  ToggleLeft,
  Variable,
  Workflow,
} from 'lucide-react'
import type {PortType} from './types'

export const categoryColors: Record<string, string> = {
  '流程': '#6385d6', '逻辑': '#9970d1', '变量': '#329caa', '时间': '#c58a27',
  '资源': '#289a77', '任务': '#d45982', '列表': '#358dcc', '调度': '#c3733f', '组合': '#7963d3',
}
export const categoryColor = (category: string) => categoryColors[category] ?? '#7b8b9e'

export const categoryIcons: Record<string, LucideIcon> = {
  '流程': Workflow,
  '逻辑': Cpu,
  '变量': Variable,
  '时间': Clock,
  '资源': Coins,
  '任务': ListTodo,
  '列表': ListFilter,
  '调度': CalendarClock,
  '组合': Layers,
}

export const cardIcons: Record<string, LucideIcon> = {
  // 流程
  entry: Play,
  end: CircleStop,
  loop: Repeat,
  foreach: Repeat1,
  loop_end: StepForward,
  call: Layers,
  input: ArrowRightToLine,
  output: ArrowRightFromLine,
  return: CornerDownLeft,
  debug: Bug,
  // 逻辑
  literal: Hash,
  compare: Scale,
  logic: Binary,
  math: Calculator,
  select: ToggleLeft,
  branch: GitBranch,
  field: Braces,
  // 变量
  get_variable: Variable,
  set_variable: PenTool,
  // 时间
  now: Clock,
  weekday: CalendarDays,
  time_window: CalendarRange,
  server_day: Sunrise,
  wait: Hourglass,
  wait_until: AlarmClock,
  // 资源
  resource: Coins,
  resource_fresh: Sparkles,
  refresh: RefreshCw,
  // 任务
  tasks: ListTodo,
  task: CheckSquare,
  last_result: History,
  requests: Radio,
  execute: Swords,
  // 列表
  filter: Filter,
  sort: ArrowUpDown,
  first: ArrowDownToDot,
  empty: CircleSlash,
  // 调度
  original_plan: Compass,
  original_settings: Settings2,
  priority: ArrowUpWideNarrow,
  oldest: Clock3,
  round_robin: Shuffle,
  cooldown: Snowflake,
  quota: Gauge,
  record: BookmarkPlus,
}

export function getCardIcon(type?: unknown, category?: string): LucideIcon {
  const key = typeof type === 'string' ? type : ''
  return cardIcons[key] ?? (category ? categoryIcons[category] : undefined) ?? Box
}
export const controlColor = '#64748b'
export const portFamily = (type: PortType) => ({duration:'number', tasks:'list', resource:'object'} as Partial<Record<PortType,PortType>>)[type] ?? type
export const compatiblePorts = (source: PortType, target: PortType) => source === 'any' || target === 'any' || portFamily(source) === portFamily(target)
export const portColors: Record<PortType, string> = {
  any: '#8191a5', number: '#329caa', boolean: '#9970d1', string: '#63a04a',
  time: '#c58a27', duration: '#329caa', resource: '#289a77', task: '#d45982',
  tasks: '#358dcc', result: '#c3733f', list: '#358dcc', object: '#289a77',
}
export const wildcardBackground = 'conic-gradient(#329caa, #9970d1, #63a04a, #c58a27, #289a77, #d45982, #358dcc, #329caa)'

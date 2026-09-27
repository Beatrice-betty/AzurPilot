import { useState, useMemo, type ReactNode } from 'react'
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Compass,
  Copy,
  Layers,
  Map as MapIcon,
  Sparkles,
  Table as TableIcon,
  Terminal,
} from 'lucide-react'
import type { LogEntry } from '../api/types'

// 提取日志消息正文（剥离 LEVEL HH:MM:SS.mmm │ 前缀）
export function extractLogMessage(raw: string): { level: string; time: string; message: string } {
  const match = /^([A-Z]{4,8})\s+(?:(\d{4}-\d{2}-\d{2})\s+)?(\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)\s*│\s*([\s\S]*)$/.exec(raw)
  if (match) {
    return { level: match[1], time: match[3], message: match[4] }
  }
  return { level: 'INFO', time: '', message: raw }
}

// 词法高亮辅助
function renderTokens(text: string, search = ''): ReactNode {
  if (!text) return null
  const searchLower = search.trim().toLowerCase()
  const tokenRegex = /(\b(?:True|False|None)\b)|(<<<[\s\S]*?>>>)|(\[[a-zA-Z0-9_.\u4e00-\u9fff-]+\])|([\{\}\[\]\(\)])|((?:[a-zA-Z]:[/\\]|(?:\.{1,2}[/\\]|[/\\]))[\w.\-/\\]+)|(\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b)/g

  const nodes: ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = tokenRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(renderSearchHighlights(text.slice(lastIndex, match.index), searchLower, `seg-${lastIndex}`))
    }
    const [full, boolVal, title, attrTag, brace, pathVal, timeVal] = match
    const key = `hl-${match.index}`

    if (boolVal) {
      const cls = boolVal === 'True' ? 'hl-bool-true' : boolVal === 'False' ? 'hl-bool-false' : 'hl-none'
      nodes.push(<span key={key} className={cls}>{renderSearchHighlights(full, searchLower, `${key}-s`)}</span>)
    } else if (title) {
      nodes.push(<span key={key} className="hl-title">{renderSearchHighlights(full, searchLower, `${key}-s`)}</span>)
    } else if (attrTag) {
      nodes.push(<span key={key} className="hl-attr">{renderSearchHighlights(full, searchLower, `${key}-s`)}</span>)
    } else if (brace) {
      nodes.push(<span key={key} className="hl-brace">{full}</span>)
    } else if (pathVal) {
      nodes.push(<span key={key} className="hl-path">{renderSearchHighlights(full, searchLower, `${key}-s`)}</span>)
    } else if (timeVal) {
      nodes.push(<span key={key} className="hl-time">{full}</span>)
    } else {
      nodes.push(renderSearchHighlights(full, searchLower, `${key}-s`))
    }
    lastIndex = match.index + full.length
  }

  if (lastIndex < text.length) {
    nodes.push(renderSearchHighlights(text.slice(lastIndex), searchLower, `seg-${lastIndex}`))
  }
  return <>{nodes}</>
}

function renderSearchHighlights(text: string, searchLower: string, keyPrefix: string): ReactNode {
  if (!text) return null
  if (!searchLower) return <span key={keyPrefix}>{text}</span>
  const lower = text.toLowerCase()
  const idx = lower.indexOf(searchLower)
  if (idx === -1) return <span key={keyPrefix}>{text}</span>

  const nodes: ReactNode[] = []
  let current = text
  let curLower = lower
  let k = 0

  while (true) {
    const matchIdx = curLower.indexOf(searchLower)
    if (matchIdx === -1) {
      if (current) nodes.push(<span key={`${keyPrefix}-t-${k}`}>{current}</span>)
      break
    }
    if (matchIdx > 0) {
      nodes.push(<span key={`${keyPrefix}-t-${k++}`}>{current.slice(0, matchIdx)}</span>)
    }
    nodes.push(<mark key={`${keyPrefix}-m-${k++}`} className="log-search-match">{current.slice(matchIdx, matchIdx + searchLower.length)}</mark>)
    current = current.slice(matchIdx + searchLower.length)
    curLower = curLower.slice(matchIdx + searchLower.length)
  }
  return <span key={keyPrefix}>{nodes}</span>
}

// 复制按钮小组件
function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  function handleCopy(e: React.MouseEvent) {
    e.stopPropagation()
    void navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button className="card-btn-action" onClick={handleCopy} title="复制内容">
      {copied ? <Check size={12} className="text-success" /> : <Copy size={12} />}
      <span>{copied ? '已复制' : label}</span>
    </button>
  )
}

// ==========================================
// 聚合卡片数据模型 (Card Aggregator Models)
// ==========================================

export type CardItem =
  | {
      type: 'map_grid'
      id: number
      time: string
      cols: string[]
      rows: Array<{ rowNum: number; cells: string[] }>
      rawText: string
    }
  | {
      type: 'cost_grid'
      id: number
      time: string
      cols: string[]
      rows: Array<{ rowNum: number; values: string[] }>
      rawText: string
    }
  | {
      type: 'matrix_grid'
      id: number
      time: string
      title: string
      rows: string[][]
      rawText: string
    }
  | {
      type: 'perspective'
      id: number
      time: string
      model: '透视' | '单应性'
      duration: string
      lowerEdge: boolean
      leftEdge: boolean
      upperEdge: boolean
      rightEdge: boolean
      info1: string
      info2: string
      rawText: string
    }
  | {
      type: 'property_sheet'
      id: number
      time: string
      items: Array<{ key: string; value: string }>
      rawText: string
    }
  | {
      type: 'data_table'
      id: number
      time: string
      title: string
      headers: string[]
      rows: string[][]
      rawText: string
    }
  | {
      type: 'error_context'
      id: number
      time: string
      title: string
      reason: string
      impact: string
      action: string
      exception: string
      stackTrace: string
      rawText: string
    }
  | {
      type: 'traceback'
      id: number
      time: string
      excName: string
      rawText: string
    }
  | {
      type: 'llm_report'
      id: number
      time: string
      model: string
      content: string
      rawText: string
    }
  | {
      type: 'system_banner'
      id: number
      time: string
      title: string
      rawText: string
    }
  | {
      type: 'stage_header'
      id: number
      time: string
      title: string
      level: 1 | 2
      rawText: string
    }
  | {
      type: 'single'
      id: number
      entry: LogEntry
      time: string
      level: string
      message: string
      rawText: string
    }

// ==========================================
// 流式块级聚合状态机 (Stream Aggregator)
// ==========================================

export function aggregateEntriesToCards(entries: LogEntry[]): CardItem[] {
  const cards: CardItem[] = []
  let index = 0

  while (index < entries.length) {
    const entry = entries[index]
    const raw = entry.text.replace(/[\r\n]+$/, '')
    const { level, time, message } = extractLogMessage(raw)
    const trimmedMsg = message.trim()

    // 1. 系统级横幅 (Level 0 HR: ═ + 居中文本 + ═)
    if (
      trimmedMsg.includes('═'.repeat(10)) &&
      index + 2 < entries.length
    ) {
      const next1 = extractLogMessage(entries[index + 1].text).message.trim()
      const next2 = extractLogMessage(entries[index + 2].text).message.trim()
      if (next2.includes('═'.repeat(10)) && next1 && !next1.includes('═')) {
        cards.push({
          type: 'system_banner',
          id: entry.id,
          time,
          title: next1,
          rawText: [entry.text, entries[index + 1].text, entries[index + 2].text].join('\n'),
        })
        index += 3
        continue
      }
    }

    // 2. 任务/阶段带线标题 (Level 1/2 HR: ═ TITLE ═ + 紧随其后的同名 INFO)
    const ruleMatch = /^[═─]{3,}\s*(.*?)\s*[═─]{3,}$/.exec(trimmedMsg)
    if (ruleMatch && ruleMatch[1].trim()) {
      const title = ruleMatch[1].trim()
      const isDouble = trimmedMsg.includes('═')
      // 检查下一行是否重复输出了同名 INFO
      let rawText = entry.text
      if (index + 1 < entries.length) {
        const nextMsg = extractLogMessage(entries[index + 1].text).message.trim()
        if (nextMsg === title) {
          rawText += '\n' + entries[index + 1].text
          index++
        }
      }
      cards.push({
        type: 'stage_header',
        id: entry.id,
        time,
        title,
        level: isDouble ? 1 : 2,
        rawText,
      })
      index++
      continue
    }

    // 3. 海域透视与边缘线识别: [地图-透视] 或 [地图-单应性]
    const perspMatch = /^\[地图-(透视|单应性)\]\s+([\d.]+s)\s+(_)?\s*(水平:.*|边缘线:.*)$/.exec(trimmedMsg)
    if (perspMatch && index + 1 < entries.length) {
      const nextMsg = extractLogMessage(entries[index + 1].text).message
      const nextPersp = /^\[地图-(透视|单应性)\]\s+边缘:\s*([\/ _\\]*?)\s+(垂直:.*|单应位置:.*)$/.exec(nextMsg.trim())
      if (nextPersp) {
        const edgesStr = nextPersp[2]
        cards.push({
          type: 'perspective',
          id: entry.id,
          time,
          model: perspMatch[1] as '透视' | '单应性',
          duration: perspMatch[2],
          lowerEdge: perspMatch[3] === '_',
          leftEdge: edgesStr.includes('/'),
          upperEdge: edgesStr.includes('_'),
          rightEdge: edgesStr.includes('\\'),
          info1: perspMatch[4],
          info2: nextPersp[3],
          rawText: entry.text + '\n' + entries[index + 1].text,
        })
        index += 2
        continue
      }
    }

    // 4. 全局海图主网格: [地图-显示]   A  B  C  D ...
    const mapHeaderMatch = /^\[地图-显示\]\s+([A-Z](\s+[A-Z])+)/.exec(trimmedMsg)
    if (mapHeaderMatch) {
      const cols = mapHeaderMatch[1].trim().split(/\s+/)
      const rows: Array<{ rowNum: number; cells: string[] }> = []
      const rawLines = [entry.text]
      let rIdx = index + 1

      while (rIdx < entries.length) {
        const rMsg = extractLogMessage(entries[rIdx].text).message
        const rowMatch = /^\s*(\d{1,2})\s+(([A-Za-z0-9_=+-]{2}\s*)+)$/.exec(rMsg)
        if (rowMatch) {
          const rowNum = parseInt(rowMatch[1], 10)
          const cells = rowMatch[2].trim().split(/\s+/)
          rows.push({ rowNum, cells })
          rawLines.push(entries[rIdx].text)
          rIdx++
        } else {
          break
        }
      }

      if (rows.length > 0) {
        cards.push({
          type: 'map_grid',
          id: entry.id,
          time,
          cols,
          rows,
          rawText: rawLines.join('\n'),
        })
        index = rIdx
        continue
      }
    }

    // 5. 寻路代价网格 (Cost Map): A B C D (大间距) + 数字行
    const costHeaderMatch = /^\s*([A-Z](\s{2,}[A-Z])+)/.exec(trimmedMsg)
    if (costHeaderMatch && index + 1 < entries.length) {
      const cols = costHeaderMatch[1].trim().split(/\s+/)
      const rows: Array<{ rowNum: number; values: string[] }> = []
      const rawLines = [entry.text]
      let rIdx = index + 1

      while (rIdx < entries.length) {
        const rMsg = extractLogMessage(entries[rIdx].text).message
        const rowMatch = /^\s*(\d{1,2})\s+(\d{1,4}(\s+\d{1,4})+)/.exec(rMsg)
        if (rowMatch) {
          const rowNum = parseInt(rowMatch[1], 10)
          const values = rowMatch[2].trim().split(/\s+/)
          rows.push({ rowNum, values })
          rawLines.push(entries[rIdx].text)
          rIdx++
        } else {
          break
        }
      }

      if (rows.length > 0) {
        cards.push({
          type: 'cost_grid',
          id: entry.id,
          time,
          cols,
          rows,
          rawText: rawLines.join('\n'),
        })
        index = rIdx
        continue
      }
    }

    // 6. 局部视野或雷达矩阵 (View.show / Radar.show): 连续多行两字符单元格（无行号）
    const matrixRowMatch = /^\s*(([A-Za-z0-9_=+-]{2}|\.\.)\s+){3,}([A-Za-z0-9_=+-]{2}|\.\.)\s*$/.exec(trimmedMsg)
    if (matrixRowMatch && !/^\s*\d+/.test(trimmedMsg)) {
      const rows: string[][] = [trimmedMsg.split(/\s+/)]
      const rawLines = [entry.text]
      let rIdx = index + 1

      while (rIdx < entries.length) {
        const rMsg = extractLogMessage(entries[rIdx].text).message.trim()
        if (/^(([A-Za-z0-9_=+-]{2}|\.\.)\s*){3,}$/.test(rMsg) && !/^\d+/.test(rMsg)) {
          rows.push(rMsg.split(/\s+/))
          rawLines.push(entries[rIdx].text)
          rIdx++
        } else {
          break
        }
      }

      if (rows.length >= 3) {
        cards.push({
          type: 'matrix_grid',
          id: entry.id,
          time,
          title: rows[0].includes('..') ? '局部海域扫描切片 (Local View)' : '大世界战略雷达扫描 (Radar Map)',
          rows,
          rawText: rawLines.join('\n'),
        })
        index = rIdx
        continue
      }
    }

    // 7. 连续右对齐属性块 (attr_align: key: val)
    const attrMatch = /^\s*([^:\n]{2,22}):\s*(.+)$/.exec(trimmedMsg)
    if (attrMatch && !trimmedMsg.startsWith('http') && !trimmedMsg.startsWith('E:') && !trimmedMsg.startsWith('C:')) {
      const items = [{ key: attrMatch[1].trim(), value: attrMatch[2].trim() }]
      const rawLines = [entry.text]
      let rIdx = index + 1

      while (rIdx < entries.length) {
        const rMsg = extractLogMessage(entries[rIdx].text).message.trim()
        const nextAttr = /^\s*([^:\n]{2,22}):\s*(.+)$/.exec(rMsg)
        if (nextAttr && !rMsg.startsWith('http') && !rMsg.startsWith('E:') && !rMsg.startsWith('C:')) {
          items.push({ key: nextAttr[1].trim(), value: nextAttr[2].trim() })
          rawLines.push(entries[rIdx].text)
          rIdx++
        } else {
          break
        }
      }

      if (items.length >= 2) {
        cards.push({
          type: 'property_sheet',
          id: entry.id,
          time,
          items,
          rawText: rawLines.join('\n'),
        })
        index = rIdx
        continue
      }
    }

    // 8. Rich Table 表格: 包含 ┌─ 和 └─
    if (raw.includes('┌') && raw.includes('┐') && raw.includes('└') && raw.includes('┘')) {
      const lines = raw.split('\n')
      let title = '数据表格 (Rich Table)'
      let headers: string[] = []
      const rows: string[][] = []

      for (let i = 0; i < lines.length; i++) {
        const l = lines[i].trim()
        if (l && !l.includes('┌') && !l.includes('│') && !l.includes('├') && !l.includes('└') && i < 2) {
          title = l
        } else if (l.includes('│') && headers.length === 0) {
          headers = l.split('│').slice(1, -1).map(s => s.trim())
        } else if (l.includes('│')) {
          const cells = l.split('│').slice(1, -1).map(s => s.trim())
          if (cells.length > 0 && !cells.every(c => !c)) {
            rows.push(cells)
          }
        }
      }

      if (headers.length > 0) {
        cards.push({
          type: 'data_table',
          id: entry.id,
          time,
          title,
          headers,
          rows,
          rawText: raw,
        })
        index++
        continue
      }
    }

    // 9. 统一错误上下文 (error_context): 包含 [错误] 与 原因/建议
    if (raw.includes('[错误]') && (raw.includes('原因：') || raw.includes('建议：'))) {
      const lines = raw.split('\n')
      let title = '运行时异常'
      let reason = ''
      let impact = ''
      let action = ''
      let exception = ''
      const stackLines: string[] = []
      let inStack = false

      for (const line of lines) {
        const trimmed = line.trim()
        if (trimmed.startsWith('[错误]')) {
          title = trimmed.replace('[错误]', '').trim()
        } else if (trimmed.startsWith('原因：')) {
          reason = trimmed.replace('原因：', '').trim()
        } else if (trimmed.startsWith('影响：')) {
          impact = trimmed.replace('影响：', '').trim()
        } else if (trimmed.startsWith('建议：')) {
          action = trimmed.replace('建议：', '').trim()
        } else if (trimmed.startsWith('异常：')) {
          exception = trimmed.replace('异常：', '').trim()
        } else if (trimmed.includes('Traceback') || inStack) {
          inStack = true
          stackLines.push(line)
        }
      }

      cards.push({
        type: 'error_context',
        id: entry.id,
        time,
        title,
        reason,
        impact,
        action,
        exception,
        stackTrace: stackLines.join('\n'),
        rawText: raw,
      })
      index++
      continue
    }

    // 10. 独立异常堆栈 (Traceback)
    if (raw.includes('Traceback (most recent call last)')) {
      const excMatch = /([a-zA-Z0-9_]+Error|[a-zA-Z0-9_]+Exception|ScriptEnd):\s*(.*)$/.exec(raw)
      cards.push({
        type: 'traceback',
        id: entry.id,
        time,
        excName: excMatch ? `${excMatch[1]}: ${excMatch[2]}` : 'Python 异常堆栈',
        rawText: raw,
      })
      index++
      continue
    }

    // 11. LLM 智能分析报告
    if (raw.includes('[LLM 分析报告') || raw.includes('[LLM] LLM 错误分析')) {
      cards.push({
        type: 'llm_report',
        id: entry.id,
        time,
        model: 'gpt-4o-mini',
        content: raw,
        rawText: raw,
      })
      index++
      continue
    }

    // 12. 默认：常规单行日志卡片
    cards.push({
      type: 'single',
      id: entry.id,
      entry,
      time,
      level,
      message,
      rawText: raw,
    })
    index++
  }

  return cards
}

// ==========================================
// 语义色彩与 Tooltip 释义词典 (Map Cell Decorators)
// ==========================================

const CELL_DICT: Record<string, { label: string; cls: string }> = {
  '++': { label: '陆地/不可通航', cls: 'cell-land' },
  '--': { label: '海洋/安全航道', cls: 'cell-sea' },
  '..': { label: '视野盲区/未检测', cls: 'cell-blind' },
  '==': { label: '已清除/已扫描', cls: 'cell-cleared' },
  'FL': { label: '第一舰队旗舰 (当前控制)', cls: 'cell-fleet-1' },
  'Fl': { label: '第二舰队', cls: 'cell-fleet-2' },
  'ss': { label: '潜艇部队', cls: 'cell-submarine' },
  'BO': { label: '关卡旗舰 Boss', cls: 'cell-boss' },
  'MY': { label: '神秘问号调查点', cls: 'cell-mystery' },
  'AM': { label: '弹药补给点', cls: 'cell-ammo' },
  'FR': { label: '机械要塞', cls: 'cell-fortress' },
  'MI': { label: '导弹支援点', cls: 'cell-missile' },
  'Fc': { label: '被塞壬捕获 (移动受限)', cls: 'cell-caught' },
  'SU': { label: '塞壬精英敌人', cls: 'cell-siren' },
  'AK': { label: '明石隐藏商店', cls: 'cell-akashi' },
  'AL': { label: '护送盟友货船', cls: 'cell-ally' },
  'RE': { label: '大世界资源箱', cls: 'cell-resource' },
  'EX': { label: '感叹号事件点', cls: 'cell-event' },
  'ME': { label: '指挥喵搜索点', cls: 'cell-meowfficer' },
  'QU': { label: '神秘问号事件', cls: 'cell-question' },
  'SD': { label: '扫描装置', cls: 'cell-device' },
}

function getCellMeta(code: string): { label: string; cls: string } {
  if (CELL_DICT[code]) return CELL_DICT[code]
  if (/^[0-3][LMCET]/.test(code)) {
    const star = code[0]
    const genreMap: Record<string, string> = { L: '轻型巡逻', M: '主力战列', C: '航空航母', E: '未知敌舰', T: '运输舰队' }
    const genre = genreMap[code[1]] || '敌舰'
    return { label: `${star}★ ${genre}敌舰`, cls: 'cell-enemy' }
  }
  return { label: `未知标记 (${code})`, cls: 'cell-default' }
}

// ==========================================
// 专用卡片组件集 (Specialized Card Components)
// ==========================================

// 1. 全局海图战术卡片
export function MapGridCard({ card }: { card: Extract<CardItem, { type: 'map_grid' }> }) {
  const [expanded, setExpanded] = useState(true)
  const shapeStr = `${card.cols.length}×${card.rows.length}`

  return (
    <div className="log-card map-card motion-enter">
      <div className="card-header" onClick={() => setExpanded(!expanded)}>
        <div className="card-title">
          <MapIcon size={16} className="text-accent" />
          <span className="title-bold">海域战术地图快照</span>
          <span className="badge-shape">{shapeStr}</span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} label="复制矩阵" />
          <button className="card-btn-icon" aria-label="展开或折叠">
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="card-body">
          <div className="map-grid-viewport">
            <table className="map-ascii-table">
              <thead>
                <tr>
                  <th className="map-th-corner">#</th>
                  {card.cols.map((col, idx) => (
                    <th key={idx} className="map-th-col">{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {card.rows.map((row) => (
                  <tr key={row.rowNum}>
                    <td className="map-td-row">{row.rowNum}</td>
                    {row.cells.map((cell, cIdx) => {
                      const meta = getCellMeta(cell)
                      return (
                        <td key={cIdx} className="map-td-cell">
                          <span
                            className={`map-badge ${meta.cls}`}
                            title={`[${card.cols[cIdx]}${row.rowNum}] ${meta.label}`}
                          >
                            {cell}
                          </span>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="map-legend-bar">
            <span className="legend-item"><span className="legend-dot dot-fleet" /> 旗舰 (FL)</span>
            <span className="legend-item"><span className="legend-dot dot-boss" /> Boss (BO)</span>
            <span className="legend-item"><span className="legend-dot dot-enemy" /> 敌舰 (1M/2C)</span>
            <span className="legend-item"><span className="legend-dot dot-mystery" /> 物资 (MY)</span>
            <span className="legend-item"><span className="legend-dot dot-land" /> 陆地 (++)</span>
            <span className="legend-item"><span className="legend-dot dot-sea" /> 海洋 (--)</span>
          </div>
        </div>
      )}
    </div>
  )
}

// 2. 海域透视与边缘线识别卡片
export function PerspectiveCard({ card }: { card: Extract<CardItem, { type: 'perspective' }> }) {
  const missingCount = [card.leftEdge, card.upperEdge, card.rightEdge, card.lowerEdge].filter(e => !e).length
  const allActive = missingCount === 0

  return (
    <div className={`log-card perspective-card ${allActive ? 'perspective-complete' : 'perspective-has-missing'} motion-enter`}>
      <div className="card-header">
        <div className="card-title">
          <Compass size={16} className={allActive ? 'text-secondary' : 'text-warning'} />
          <span className="title-bold">海域{card.model}与边界线拓扑</span>
          <span className="badge-pill duration">{card.duration}</span>
          <span className={`badge-pill ${allActive ? 'edge-status-full' : 'edge-status-missing'}`}>
            {allActive ? '4 边完整闭合' : `缺失 ${missingCount} 边`}
          </span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} />
        </div>
      </div>
      <div className="card-body perspective-body">
        {/* 梯形视口微型几何模型 (向前倾斜，近大远小) */}
        <div className="trapezoid-visual" title="海域 2.5D 透视边界视口 (向前倾斜)">
          <svg width="168" height="96" viewBox="0 0 168 96" className="trapezoid-svg">
            {/* 梯形底面浅色半透明背景 (仅完全闭合时填充) */}
            <polygon
              points="48,20 120,20 152,78 16,78"
              className={`trapezoid-fill ${allActive ? 'fill-all' : 'fill-broken'}`}
            />
            {/* 纵深透视虚线网格（向前方地平线收拢） */}
            <line x1="66" y1="20" x2="52" y2="78" className="grid-depth-line" />
            <line x1="84" y1="20" x2="84" y2="78" className="grid-depth-line" />
            <line x1="102" y1="20" x2="116" y2="78" className="grid-depth-line" />
            <line x1="33" y1="49" x2="135" y2="49" className="grid-depth-line" />

            {/* 上边界 (远处，较短) */}
            <line
              x1="48" y1="20" x2="120" y2="20"
              className={`edge-stroke ${card.upperEdge ? 'edge-active' : 'edge-missing'}`}
            />
            <text x="84" y="13" textAnchor="middle" className={`edge-svg-text ${card.upperEdge ? 'text-active' : 'text-missing'}`}>
              {card.upperEdge ? '上边界' : '上(缺失)'}
            </text>

            {/* 下边界 (近处，较宽) */}
            <line
              x1="16" y1="78" x2="152" y2="78"
              className={`edge-stroke ${card.lowerEdge ? 'edge-active' : 'edge-missing'}`}
            />
            <text x="84" y="90" textAnchor="middle" className={`edge-svg-text ${card.lowerEdge ? 'text-active' : 'text-missing'}`}>
              {card.lowerEdge ? '下边界' : '下(缺失)'}
            </text>

            {/* 左边界 (向前倾斜收拢) */}
            <line
              x1="16" y1="78" x2="48" y2="20"
              className={`edge-stroke ${card.leftEdge ? 'edge-active' : 'edge-missing'}`}
            />
            <text x="20" y="47" textAnchor="middle" className={`edge-svg-text ${card.leftEdge ? 'text-active' : 'text-missing'}`}>
              {card.leftEdge ? '左' : '左(缺失)'}
            </text>

            {/* 右边界 (向前倾斜收拢) */}
            <line
              x1="120" y1="20" x2="152" y2="78"
              className={`edge-stroke ${card.rightEdge ? 'edge-active' : 'edge-missing'}`}
            />
            <text x="148" y="47" textAnchor="middle" className={`edge-svg-text ${card.rightEdge ? 'text-active' : 'text-missing'}`}>
              {card.rightEdge ? '右' : '右(缺失)'}
            </text>
          </svg>
        </div>

        {/* 识别指标清单 */}
        <div className="perspective-metrics">
          <div className="metric-row">
            <span className="metric-label">水平状态:</span>
            <span className="metric-val">{card.info1}</span>
          </div>
          <div className="metric-row">
            <span className="metric-label">垂直/定位:</span>
            <span className="metric-val">{card.info2}</span>
          </div>
          <div className="metric-tags">
            <span className={`status-chip ${card.leftEdge ? 'active' : 'missing'}`}>
              左边缘 {card.leftEdge ? '✓ 可见' : '✗ 缺失'}
            </span>
            <span className={`status-chip ${card.upperEdge ? 'active' : 'missing'}`}>
              上边缘 {card.upperEdge ? '✓ 可见' : '✗ 缺失'}
            </span>
            <span className={`status-chip ${card.rightEdge ? 'active' : 'missing'}`}>
              右边缘 {card.rightEdge ? '✓ 可见' : '✗ 缺失'}
            </span>
            <span className={`status-chip ${card.lowerEdge ? 'active' : 'missing'}`}>
              下边缘 {card.lowerEdge ? '✓ 可见' : '✗ 缺失'}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

// 3. 连续属性对齐卡片 (Property Sheet)
export function PropertySheetCard({ card, search }: { card: Extract<CardItem, { type: 'property_sheet' }>; search: string }) {
  return (
    <div className="log-card property-card motion-enter">
      <div className="card-header">
        <div className="card-title">
          <Layers size={15} className="text-muted" />
          <span className="title-bold">状态属性清单</span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} />
        </div>
      </div>
      <div className="card-body">
        <div className="property-grid">
          {card.items.map((item, idx) => (
            <div key={idx} className="property-row">
              <span className="prop-key">{item.key}</span>
              <span className="prop-divider">:</span>
              <span className="prop-val">{renderTokens(item.value, search)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// 4. 原生数据表格卡片 (Benchmark / Score)
export function DataTableCard({ card }: { card: Extract<CardItem, { type: 'data_table' }> }) {
  return (
    <div className="log-card table-card motion-enter">
      <div className="card-header">
        <div className="card-title">
          <TableIcon size={16} className="text-accent" />
          <span className="title-bold">{card.title}</span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} />
        </div>
      </div>
      <div className="card-body">
        <div className="data-table-viewport">
          <table className="native-log-table">
            <thead>
              <tr>
                {card.headers.map((h, i) => (
                  <th key={i}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {card.rows.map((row, rIdx) => (
                <tr key={rIdx}>
                  {row.map((cell, cIdx) => (
                    <td key={cIdx}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// 5. 四段式统一错误上下文卡片 (error_context)
export function ErrorContextCard({ card }: { card: Extract<CardItem, { type: 'error_context' }> }) {
  const [stackOpen, setStackOpen] = useState(false)

  return (
    <div className="log-card error-card motion-enter">
      <div className="card-header error-header">
        <div className="card-title">
          <AlertCircle size={18} className="text-danger" />
          <span className="title-bold error-title">{card.title}</span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} label="复制错误现场" />
        </div>
      </div>

      <div className="card-body error-body">
        {card.reason && (
          <div className="error-section">
            <span className="error-tag tag-reason">原因</span>
            <span className="error-text">{card.reason}</span>
          </div>
        )}
        {card.impact && (
          <div className="error-section">
            <span className="error-tag tag-impact">影响</span>
            <span className="error-text">{card.impact}</span>
          </div>
        )}
        {card.action && (
          <div className="error-section section-action">
            <span className="error-tag tag-action">建议操作</span>
            <span className="error-text text-action">{card.action}</span>
          </div>
        )}
        {card.exception && (
          <div className="error-section">
            <span className="error-tag tag-exc">底层异常</span>
            <span className="error-text text-mono text-muted">{card.exception}</span>
          </div>
        )}

        {card.stackTrace && (
          <div className="error-stack-wrapper">
            <button className="stack-toggle-btn" onClick={() => setStackOpen(!stackOpen)}>
              {stackOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span>{stackOpen ? '收起完整堆栈' : '展开完整堆栈追踪 (Traceback)'}</span>
            </button>
            {stackOpen && (
              <pre className="error-stack-content log-multiline-container">{card.stackTrace}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// 6. 异常堆栈卡片 (Traceback)
export function TracebackCard({ card }: { card: Extract<CardItem, { type: 'traceback' }> }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="log-card traceback-card motion-enter">
      <div className="card-header" onClick={() => setOpen(!open)}>
        <div className="card-title">
          <Terminal size={15} className="text-warning" />
          <span className="title-bold">{card.excName}</span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} />
          <button className="card-btn-icon" aria-label="展开或折叠">
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </div>
      </div>
      {open && (
        <div className="card-body">
          <pre className="log-multiline-container traceback-pre">{card.rawText}</pre>
        </div>
      )}
    </div>
  )
}

// 7. LLM 智能分析报告卡片
export function LlmReportCard({ card }: { card: Extract<CardItem, { type: 'llm_report' }> }) {
  return (
    <div className="log-card llm-card motion-enter">
      <div className="card-header llm-header">
        <div className="card-title">
          <Sparkles size={16} className="text-llm" />
          <span className="title-bold">AI 错误智能分析诊断报告</span>
          <span className="badge-pill llm-model">{card.model}</span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} />
        </div>
      </div>
      <div className="card-body">
        <pre className="llm-markdown-text">{card.content}</pre>
      </div>
    </div>
  )
}

// 8. 矩阵切片卡片 (Radar / View)
export function MatrixGridCard({ card }: { card: Extract<CardItem, { type: 'matrix_grid' }> }) {
  const [open, setOpen] = useState(true)

  return (
    <div className="log-card matrix-card motion-enter">
      <div className="card-header" onClick={() => setOpen(!open)}>
        <div className="card-title">
          <MapIcon size={15} className="text-secondary" />
          <span className="title-bold">{card.title}</span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} />
          <button className="card-btn-icon" aria-label="展开或折叠">
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </div>
      </div>
      {open && (
        <div className="card-body">
          <div className="map-grid-viewport">
            <div className="matrix-grid-rows">
              {card.rows.map((row, rIdx) => (
                <div key={rIdx} className="matrix-row">
                  {row.map((cell, cIdx) => {
                    const meta = getCellMeta(cell)
                    return (
                      <span key={cIdx} className={`map-badge ${meta.cls}`} title={meta.label}>
                        {cell}
                      </span>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// 9. 寻路代价网格卡片 (Cost Grid)
export function CostGridCard({ card }: { card: Extract<CardItem, { type: 'cost_grid' }> }) {
  const [open, setOpen] = useState(true)

  return (
    <div className="log-card cost-card motion-enter">
      <div className="card-header" onClick={() => setOpen(!open)}>
        <div className="card-title">
          <Compass size={15} className="text-accent" />
          <span className="title-bold">寻路移动代价热力图 (Cost Map)</span>
          <span className="card-time">{card.time}</span>
        </div>
        <div className="card-actions">
          <CopyButton text={card.rawText} />
          <button className="card-btn-icon" aria-label="展开或折叠">
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </div>
      </div>
      {open && (
        <div className="card-body">
          <div className="map-grid-viewport">
            <table className="map-ascii-table cost-table">
              <thead>
                <tr>
                  <th className="map-th-corner">#</th>
                  {card.cols.map((col, idx) => (
                    <th key={idx} className="map-th-col">{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {card.rows.map((row) => (
                  <tr key={row.rowNum}>
                    <td className="map-td-row">{row.rowNum}</td>
                    {row.values.map((val, cIdx) => {
                      const num = parseInt(val, 10)
                      const isObstacle = num >= 9999
                      const isOrigin = num === 0
                      const cls = isOrigin ? 'cost-origin' : isObstacle ? 'cost-wall' : 'cost-path'
                      return (
                        <td key={cIdx} className="map-td-cell">
                          <span className={`cost-cell ${cls}`} title={isObstacle ? '不可达障碍' : `步数代价: ${num}`}>
                            {val}
                          </span>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// 10. 系统横幅卡片
export function SystemBannerCard({ card }: { card: Extract<CardItem, { type: 'system_banner' }> }) {
  return (
    <div className="log-card system-banner-card motion-enter">
      <div className="banner-double-rule" />
      <div className="banner-title-text">{card.title}</div>
      <div className="banner-double-rule" />
    </div>
  )
}

// 11. 任务阶段卡片
export function StageHeaderCard({ card }: { card: Extract<CardItem, { type: 'stage_header' }> }) {
  return (
    <div className={`log-card stage-header-card level-${card.level} motion-enter`}>
      <div className="stage-rule-bar" />
      <div className="stage-title-wrap">
        <span className="stage-title">{card.title}</span>
        {card.time && <span className="stage-time">{card.time}</span>}
      </div>
      <div className="stage-rule-bar" />
    </div>
  )
}

// 12. 常规单行日志行
export function SingleLogLineCard({ card, search }: { card: Extract<CardItem, { type: 'single' }>; search: string }) {
  const lvlKey = card.level.toLowerCase()
  return (
    <div className={`log-line log-entry-line level-${lvlKey} log-card-line motion-enter`}>
      <span className={`log-lvl lvl-${lvlKey}`}>{card.level}</span>
      <span className="log-ts">{card.time}</span>
      <span className="log-divider">│</span>
      <span className="log-msg">{renderTokens(card.message, search)}</span>
    </div>
  )
}

// ==========================================
// 统一卡片模式渲染主容器 (LogCardView Container)
// ==========================================

export function LogCardView({
  entries,
  search,
}: {
  entries: LogEntry[]
  search: string
}) {
  const cards = useMemo(() => aggregateEntriesToCards(entries), [entries])

  return (
    <div className="log-cards-container">
      {cards.map((card) => {
        switch (card.type) {
          case 'map_grid':
            return <MapGridCard key={card.id} card={card} />
          case 'perspective':
            return <PerspectiveCard key={card.id} card={card} />
          case 'cost_grid':
            return <CostGridCard key={card.id} card={card} />
          case 'matrix_grid':
            return <MatrixGridCard key={card.id} card={card} />
          case 'property_sheet':
            return <PropertySheetCard key={card.id} card={card} search={search} />
          case 'data_table':
            return <DataTableCard key={card.id} card={card} />
          case 'error_context':
            return <ErrorContextCard key={card.id} card={card} />
          case 'traceback':
            return <TracebackCard key={card.id} card={card} />
          case 'llm_report':
            return <LlmReportCard key={card.id} card={card} />
          case 'system_banner':
            return <SystemBannerCard key={card.id} card={card} />
          case 'stage_header':
            return <StageHeaderCard key={card.id} card={card} />
          case 'single':
          default:
            return <SingleLogLineCard key={card.id} card={card} search={search} />
        }
      })}
    </div>
  )
}

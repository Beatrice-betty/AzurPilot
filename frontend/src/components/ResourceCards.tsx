import { useState } from 'react'
import { ArrowDown, ArrowUp, Box, Coins, Droplets, Gem, Medal, Settings2, Ticket, Trophy, Zap } from 'lucide-react'
import type { Resource } from '../api/types'
import { Modal } from './ui'

export const resourceLabels: Record<string, string> = {Oil: '石油', Coin: '物资', Gem: '钻石', Cube: '心智魔方', Pt: '活动 PT', ActionPoint: '行动力', YellowCoin: '作战补给凭证', PurpleCoin: '特别兑换凭证', Core: '核心数据', Medal: '荣誉勋章', Merit: '功勋', GuildCoin: '舰队币', Chip: '心智单元'}
const icons: Record<string, typeof Box> = {Oil: Droplets, Coin: Coins, Gem, Cube: Box, ActionPoint: Zap, Medal, Merit: Trophy, YellowCoin: Ticket, PurpleCoin: Ticket}
const defaults = ['Oil', 'Coin', 'Gem', 'Cube']

export function ResourceCards({instance, resources}: {instance: string; resources: Resource[]}) {
  const storageKey = `azurpilot.resources.${instance}`
  const [selected, setSelected] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
      if (Array.isArray(saved) && saved.every(item => typeof item === 'string')) return [...new Set(saved)]
    } catch { /* 损坏偏好使用默认搭配。 */ }
    return defaults
  })
  const [editing, setEditing] = useState(false)
  function update(keys: string[]) {
    setSelected(keys)
    try { localStorage.setItem(storageKey, JSON.stringify(keys)) } catch { /* 无存储权限时仅本页生效。 */ }
  }
  function move(index: number, offset: number) {
    const keys = [...selected]; [keys[index], keys[index + offset]] = [keys[index + offset], keys[index]]; update(keys)
  }
  return <><div className="resources-toolbar"><span>资源概况 · {selected.length} 项</span><button className="text-button" onClick={() => setEditing(true)}><Settings2 size={14}/>配置资源</button></div>
    <div className="resource-grid">{selected.map((key, index) => {
      const resource = resources.find(item => item.name === key)
      const Icon = icons[key] ?? Box
      const recorded = resource?.record && !resource.record.startsWith('2020-01-01')
      return <section key={key} className={`resource-card resource-${index % 4}`}><div className="resource-heading"><span>{resourceLabels[key] ?? resource?.label ?? key}</span><div><Icon size={18}/></div></div><div className="resource-value">{recorded && resource?.value != null ? resource.value.toLocaleString() : '—'}{recorded && !!resource?.limit && <small>/ {resource.limit.toLocaleString()}</small>}</div><div className="resource-foot">{recorded ? `记录于 ${resource.record?.replace('T', ' ').slice(5, 19)}` : '等待游戏内资源同步'}</div></section>
    })}</div>{editing && <Modal title="配置资源卡片" onClose={() => setEditing(false)}><div className="resource-options">{resources.map(resource => <label key={resource.name}><input type="checkbox" checked={selected.includes(resource.name)} onChange={event => update(event.target.checked ? [...selected, resource.name] : selected.filter(key => key !== resource.name))}/>{resourceLabels[resource.name] ?? resource.label}</label>)}</div><div className="resource-order">{selected.map((key, index) => <div key={key}><span>{resourceLabels[key] ?? key}</span><button className="icon-button" disabled={!index} aria-label={`上移${resourceLabels[key] ?? key}`} onClick={() => move(index, -1)}><ArrowUp size={14}/></button><button className="icon-button" disabled={index === selected.length - 1} aria-label={`下移${resourceLabels[key] ?? key}`} onClick={() => move(index, 1)}><ArrowDown size={14}/></button></div>)}</div><div className="title-actions"><button className="button secondary" onClick={() => update(defaults)}>恢复默认</button><button className="button primary" onClick={() => setEditing(false)}>完成</button></div></Modal>}</>
}

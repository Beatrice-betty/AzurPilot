import { useState } from 'react'
import { Box, GripVertical, Plus, X } from 'lucide-react'
import type { Resource } from '../api/types'

export const resourceLabels: Record<string, string> = {Oil: '石油', Coin: '物资', Gem: '钻石', Cube: '心智魔方', Pt: '活动 PT', ActionPoint: '行动力', YellowCoin: '作战补给凭证', PurpleCoin: '特别兑换凭证', Core: '核心数据', Medal: '荣誉勋章', Merit: '功勋', GuildCoin: '舰队币', Chip: '心智单元'}
const iconImages: Record<string, string> = {
  Oil: '/oil.webp',
  Coin: '/gold.webp',
  Gem: '/diamond.webp',
  Cube: '/cube.webp',
  Pt: '/pt.webp',
  ActionPoint: '/guild_coin.webp',
  YellowCoin: '/supply_token.webp',
  PurpleCoin: '/special_token.webp',
  Core: '/core_data.webp',
  Medal: '/honor_medal.webp',
  Merit: '/merit.webp',
  GuildCoin: '/stamina.webp',
}

function ResourceIcon({resourceKey, size = 32}: {resourceKey: string; size?: number}) {
  const src = iconImages[resourceKey]
  return src ? <img className="resource-icon-image" src={src} alt="" width={size} height={size}/> : <Box size={Math.round(size * .62)}/>
}
export const defaultResourceKeys = ['Oil', 'Coin', 'Gem', 'Cube']

export function ResourceCards({resources, selected}: {resources: Resource[]; selected: string[]}) {
  return <div className="resource-grid">{selected.map((key, index) => {
      const resource = resources.find(item => item.name === key)
      const recorded = resource?.record && !resource.record.startsWith('2020-01-01')
      return <section key={key} className={`resource-card resource-${index % 4}`}><div className="resource-heading"><span>{resourceLabels[key] ?? resource?.label ?? key}</span><div className="resource-image-wrap"><ResourceIcon resourceKey={key} size={32}/></div></div><div className="resource-value">{recorded && resource?.value != null ? resource.value.toLocaleString() : '—'}{recorded && !!resource?.limit && <small>/ {resource.limit.toLocaleString()}</small>}</div><div className="resource-foot">{recorded ? `记录于 ${resource.record?.replace('T', ' ').slice(5, 19)}` : '等待游戏内资源同步'}</div></section>
    })}</div>
}

export function ResourceSettings({resources, selected, onChange}: {resources: Resource[]; selected: string[]; onChange: (keys: string[]) => void}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [draggingKey, setDraggingKey] = useState<string | null>(null)
  const available = resources.filter(resource => !selected.includes(resource.name))

  function add(key: string) {
    if (!selected.includes(key)) onChange([...selected, key])
  }
  function remove(key: string) {
    onChange(selected.filter(item => item !== key))
  }
  function move(fromKey: string, toKey: string) {
    if (fromKey === toKey) return
    const from = selected.indexOf(fromKey)
    const to = selected.indexOf(toKey)
    if (from < 0 || to < 0) return
    const keys = [...selected]
    const [moved] = keys.splice(from, 1)
    keys.splice(to, 0, moved)
    onChange(keys)
  }

  return <div className="resource-settings">
    <div className="resource-settings-heading"><div><strong>资源卡片</strong><span>点击添加卡片，拖拽卡片调整显示顺序</span></div><button type="button" className="text-button" onClick={() => onChange(defaultResourceKeys)}>恢复默认</button></div>
    <div className="resource-card-editor">
      {selected.map(key => {
        const resource = resources.find(item => item.name === key)
          const label = resourceLabels[key] ?? resource?.label ?? key
        return <div key={key} className={`resource-editor-card${draggingKey === key ? ' dragging' : ''}`} draggable onDragStart={event => {setDraggingKey(key); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', key)}} onDragEnd={() => setDraggingKey(null)} onDragOver={event => {event.preventDefault(); event.dataTransfer.dropEffect = 'move'}} onDrop={event => {event.preventDefault(); const source = draggingKey ?? event.dataTransfer.getData('text/plain'); if (source) move(source, key); setDraggingKey(null)}}>
          <span className="resource-editor-grip" aria-hidden="true"><GripVertical size={16}/></span>
          <span className="resource-editor-icon resource-editor-icon-image"><ResourceIcon resourceKey={key} size={30}/></span>
          <span className="resource-editor-label">{label}</span>
          <button type="button" className="resource-editor-remove" aria-label={`移除${label}`} title={`移除${label}`} onClick={() => remove(key)}><X size={15}/></button>
        </div>
      })}
      <button type="button" className={`resource-editor-card resource-editor-add${pickerOpen ? ' open' : ''}`} onClick={() => setPickerOpen(open => !open)}><span className="resource-editor-add-icon"><Plus size={18}/></span><span>添加卡片</span></button>
    </div>
    {pickerOpen && <div className="resource-picker">{available.length ? <div className="resource-picker-grid">{available.map(resource => {
      const label = resourceLabels[resource.name] ?? resource.label ?? resource.name
      return <button type="button" key={resource.name} className="resource-picker-card" onClick={() => add(resource.name)}><span className="resource-editor-icon resource-editor-icon-image"><ResourceIcon resourceKey={resource.name} size={28}/></span><span>{label}</span><Plus size={15}/></button>
    })}</div> : <div className="resource-picker-empty">所有可用资源都已经添加了</div>}</div>}
  </div>
}

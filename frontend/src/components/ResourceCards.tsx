import { useRef, useState } from 'react'
import { Box, GripVertical, Plus, X } from 'lucide-react'
import type { Resource } from '../api/types'
import { useApp } from '../app/context'
import type { UiKey } from '../i18n'

export const resourceLabels: Record<string, UiKey> = {Oil: 'resource.Oil', Coin: 'resource.Coin', Gem: 'resource.Gem', Cube: 'resource.Cube', Pt: 'resource.Pt', ActionPoint: 'resource.ActionPoint', YellowCoin: 'resource.YellowCoin', PurpleCoin: 'resource.PurpleCoin', Core: 'resource.Core', Medal: 'resource.Medal', Merit: 'resource.Merit', GuildCoin: 'resource.GuildCoin', Chip: 'resource.Chip'}
const iconBase = import.meta.env.BASE_URL
const iconImages: Record<string, string> = {
  Oil: `${iconBase}oil.webp`,
  Coin: `${iconBase}gold.webp`,
  Gem: `${iconBase}diamond.webp`,
  Cube: `${iconBase}cube.webp`,
  Pt: `${iconBase}pt.webp`,
  ActionPoint: `${iconBase}guild_coin.webp`,
  YellowCoin: `${iconBase}supply_token.webp`,
  PurpleCoin: `${iconBase}special_token.webp`,
  Core: `${iconBase}core_data.webp`,
  Medal: `${iconBase}honor_medal.webp`,
  Merit: `${iconBase}merit.webp`,
  GuildCoin: `${iconBase}stamina.webp`,
}

function ResourceIcon({resourceKey, size = 32}: {resourceKey: string; size?: number}) {
  const src = iconImages[resourceKey]
  return src ? <img className="resource-icon-image" src={src} alt="" width={size} height={size} draggable={false}/> : <Box size={Math.round(size * .62)}/>
}
export const defaultResourceKeys = ['Oil', 'Coin', 'Gem', 'Cube']

export function moveResourceKey(keys: string[], fromKey: string, toKey: string): string[] {
  if (fromKey === toKey) return keys
  const from = keys.indexOf(fromKey)
  const to = keys.indexOf(toKey)
  if (from < 0 || to < 0) return keys
  const next = [...keys]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

export function ResourceCards({resources, selected}: {resources: Resource[]; selected: string[]}) {
  const {ui} = useApp()
  return <div className="resource-grid">{selected.map((key, index) => {
      const resource = resources.find(item => item.name === key)
      const recorded = resource?.record && !resource.record.startsWith('2020-01-01')
      const labelKey = resourceLabels[key]
      const label = labelKey ? ui(labelKey) : resource?.label ?? key
      const limit = resource?.limit
      const total = resource?.total
      const showLimit = typeof limit === 'number' && limit > 0
      const showTotal = !!recorded && !showLimit && resource?.name === 'ActionPoint' && typeof resource.value === 'number' && typeof total === 'number' && Number.isFinite(total) && total >= resource.value
      const value = resource?.value
      const displayValue = recorded && value != null ? `${value.toLocaleString()}${showTotal ? `/${total.toLocaleString()}` : ''}` : '—'
      return <section key={key} className={`resource-card resource-${index % 4}`}>
        <div className="resource-heading"><span>{label}</span><div className="resource-image-wrap"><ResourceIcon resourceKey={key} size={32}/></div></div>
        <div className="resource-value">
          <span>{displayValue}</span>
          {recorded && showLimit && <small>/ {limit.toLocaleString()}</small>}
        </div>
        <div className="resource-foot">{recorded ? ui('resource.recordedAt', {time: resource.record?.replace('T', ' ').slice(5, 19) ?? ''}) : ui('resource.waitingSync')}</div>
      </section>
    })}</div>
}

export function ResourceSettings({resources, selected, onChange}: {resources: Resource[]; selected: string[]; onChange: (keys: string[]) => void}) {
  const {ui} = useApp()
  const [pickerOpen, setPickerOpen] = useState(false)
  const [draggingKey, setDraggingKey] = useState<string | null>(null)
  const [dragOrder, setDragOrder] = useState<string[] | null>(null)
  const dragOrderRef = useRef<string[] | null>(null)
  const dragPointerRef = useRef<number | null>(null)
  const dragKeyRef = useRef<string | null>(null)
  const available = resources.filter(resource => !selected.includes(resource.name))
  const displayed = dragOrder ?? selected

  function add(key: string) {
    if (!selected.includes(key)) onChange([...selected, key])
  }
  function remove(key: string) {
    onChange(selected.filter(item => item !== key))
  }
  function finishDrag(pointerId: number, apply: boolean) {
    if (dragPointerRef.current !== pointerId) return
    const next = dragOrderRef.current
    dragPointerRef.current = null
    dragOrderRef.current = null
    dragKeyRef.current = null
    setDraggingKey(null)
    setDragOrder(null)
    if (apply && next && next.some((key, index) => key !== selected[index])) onChange(next)
  }

  return <div className="resource-settings">
    <div className="resource-settings-heading"><div><strong>{ui('resource.cards')}</strong><span>{ui('resource.cardsHint')}</span></div><button type="button" className="text-button" onClick={() => onChange(defaultResourceKeys)}>{ui('resource.restoreDefault')}</button></div>
    <div className="resource-card-editor">
      {displayed.map(key => {
        const resource = resources.find(item => item.name === key)
          const labelKey = resourceLabels[key]
          const label = labelKey ? ui(labelKey) : resource?.label ?? key
        return <div key={key} data-resource-key={key} className={`resource-editor-card${draggingKey === key ? ' dragging' : ''}`}
          onPointerDown={event => {
            if (event.button !== 0 || (event.target as HTMLElement).closest('button')) return
            event.preventDefault()
            event.currentTarget.setPointerCapture(event.pointerId)
            dragPointerRef.current = event.pointerId
            dragKeyRef.current = key
            dragOrderRef.current = [...selected]
            setDragOrder([...selected])
            setDraggingKey(key)
          }}
          onPointerMove={event => {
            const sourceKey = dragKeyRef.current
            if (dragPointerRef.current !== event.pointerId || !sourceKey) return
            const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-resource-key]')?.dataset.resourceKey
            if (!target || !dragOrderRef.current) return
            const next = moveResourceKey(dragOrderRef.current, sourceKey, target)
            if (next === dragOrderRef.current) return
            dragOrderRef.current = next
            setDragOrder(next)
          }}
          onPointerUp={event => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
            finishDrag(event.pointerId, true)
          }}
          onPointerCancel={event => finishDrag(event.pointerId, false)}>
          <span className="resource-editor-grip" aria-hidden="true"><GripVertical size={16}/></span>
          <span className="resource-editor-icon resource-editor-icon-image"><ResourceIcon resourceKey={key} size={30}/></span>
          <span className="resource-editor-label">{label}</span>
          <button type="button" className="resource-editor-remove" aria-label={ui('resource.remove', {label})} title={ui('resource.remove', {label})} onClick={() => remove(key)}><X size={15}/></button>
        </div>
      })}
      <button type="button" className={`resource-editor-card resource-editor-add${pickerOpen ? ' open' : ''}`} onClick={() => setPickerOpen(open => !open)}><span className="resource-editor-add-icon"><Plus size={18}/></span><span>{ui('resource.addCard')}</span></button>
    </div>
    {pickerOpen && <div className="resource-picker">{available.length ? <div className="resource-picker-grid">{available.map(resource => {
      const labelKey = resourceLabels[resource.name]
      const label = labelKey ? ui(labelKey) : resource.label ?? resource.name
      return <button type="button" key={resource.name} className="resource-picker-card" onClick={() => add(resource.name)}><span className="resource-editor-icon resource-editor-icon-image"><ResourceIcon resourceKey={resource.name} size={28}/></span><span>{label}</span><Plus size={15}/></button>
    })}</div> : <div className="resource-picker-empty">{ui('resource.allAdded')}</div>}</div>}
  </div>
}

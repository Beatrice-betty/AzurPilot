import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { Check, ChevronDown, Plus, Ship } from 'lucide-react'
import { useApp, useConnection } from '../app/context'

export function InstanceSwitcher({onCreate}: {onCreate: () => void}) {
  const {instances} = useApp()
  const {instance} = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const connection = useConnection()
  const [open, setOpen] = useState(false)
  const container = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    container.current?.querySelector<HTMLButtonElement>('[aria-checked="true"], [role="menuitemradio"], [role="menuitem"]')?.focus()
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])
  useEffect(() => {setOpen(false)}, [location.pathname])
  return <div className="instance-picker" ref={container} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
  }} onKeyDown={event => {
    if (event.key === 'Escape') {setOpen(false); trigger.current?.focus()}
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    if (!open) {setOpen(true); return}
    const items = Array.from(container.current!.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]:not(:disabled)'))
    const current = items.indexOf(document.activeElement as HTMLButtonElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
    items[next]?.focus()
  }}>
    <button ref={trigger} className="instance-switcher" aria-label="切换实例" aria-haspopup="menu" aria-expanded={open} aria-controls="instance-menu" onClick={() => setOpen(!open)}>
      <span className="instance-caption"><strong>{instance}</strong></span><ChevronDown size={12}/>
    </button>
    {open && <div className="instance-menu" id="instance-menu" role="menu" aria-label="配置实例">
      <div className="instance-options">{instances.map(item => <button key={item.name} role="menuitemradio" aria-checked={item.name === instance} onClick={() => {
        setOpen(false); trigger.current?.focus()
        if (item.name !== instance) navigate(`/i/${item.name}/${location.pathname.split('/').slice(3).join('/') || 'overview'}`)
      }}><Ship size={16}/><span>{item.name}</span>{item.name === instance && <Check size={16}/>}</button>)}</div>
      <button className="instance-create" role="menuitem" aria-label="创建实例" title="创建实例" disabled={connection !== 'ready'} onClick={() => {setOpen(false); onCreate()}}><Plus size={20}/></button>
    </div>}
  </div>
}

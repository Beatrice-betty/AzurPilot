import type { Value } from '../api/types'
import { lazy, Suspense } from 'react'
import { AutoTextarea } from './AutoTextarea'

const YamlEditor = lazy(() => import('./YamlEditor').then(module => ({default: module.YamlEditor})))

interface Props {
  id: string; value: Value; onChange: (value: Value) => void; type?: string
  options?: Value[]; disabled?: boolean; label: string; mode?: string; translateOption?: (value: Value) => string
  preserveText?: boolean; invalid?: boolean
}
export function FieldInput({id, value, onChange, type, options, disabled, label, mode, translateOption, preserveText, invalid}: Props) {
  // 只读时间沿用旧界面的原始文本，保留秒、小数秒和历史格式。
  if (type === 'datetime' && disabled) return <input id={id} aria-label={label} readOnly value={String(value ?? '').replace('T', ' ')} />
  if (mode === 'yaml' || type === 'yaml') return <Suspense fallback={<div role="status">正在加载编辑器…</div>}><YamlEditor id={id} value={String(value ?? '')} onChange={onChange} disabled={disabled} label={label}/></Suspense>
  if (type === 'multiselect') return <div className="multi-options" id={id} role="group" aria-label={label}>{options?.map(option => {
    const selected = Array.isArray(value) ? value : []
    const checked = selected.includes(option as never)
    return <label key={JSON.stringify(option)}><input type="checkbox" checked={checked} disabled={disabled} onChange={() => onChange(checked ? selected.filter(item => item !== option) : [...selected, option] as Value)}/>{translateOption?.(option) ?? String(option)}</label>
  })}</div>
  if (type === 'checkbox' || type === 'bool' || typeof value === 'boolean') {
    return <button id={id} type="button" role="switch" aria-label={label} aria-checked={!!value} disabled={disabled}
      className={`toggle ${value ? 'on' : ''}`} onClick={() => onChange(!value)}><span /></button>
  }
  if (options?.length) {
    return <select id={id} value={JSON.stringify(value)} disabled={disabled} onChange={event => onChange(JSON.parse(event.target.value))}>
      {!options.some(option => option === value) && <option value={JSON.stringify(value)}>{String(value ?? '未设置')}</option>}
      {options.map(option => <option value={JSON.stringify(option)} key={JSON.stringify(option)}>{translateOption?.(option) ?? String(option)}</option>)}
    </select>
  }
  if (type === 'textarea' || type === 'task_priority') return <AutoTextarea id={id} value={String(value ?? '')} disabled={disabled} onChange={onChange}/>
  const isNumber = typeof value === 'number' || ['int', 'number', 'float'].includes(type ?? '')
  return <input id={id} disabled={disabled} aria-invalid={invalid || undefined} aria-describedby={`${id}-status`}
    inputMode={isNumber ? 'decimal' : undefined}
    type={type === 'password' ? 'password' : type === 'datetime' && !preserveText ? 'datetime-local' : isNumber && !preserveText ? 'number' : 'text'}
    step={type === 'datetime' ? 1 : 'any'} autoComplete={type === 'password' ? 'new-password' : 'off'}
    value={type === 'datetime' && !preserveText ? String(value ?? '').replace(' ', 'T').slice(0, 23) : value === null ? '' : String(value)}
    onChange={event => {
      const next = event.target.value
      onChange(preserveText ? next : type === 'datetime' ? (next.length === 16 ? `${next.replace('T', ' ')}:00` : next.replace('T', ' ')) : isNumber && next !== '' ? Number(next) : next)
    }}/>
}

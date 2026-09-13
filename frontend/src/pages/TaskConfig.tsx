import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Play, Search, Settings2, Ship } from 'lucide-react'
import { api } from '../api/client'
import type { Config, Value } from '../api/types'
import { useApp, useConnection } from '../app/context'
import { Empty, ErrorBox, Loading, Modal, PageTitle } from '../components/ui'
import { FieldInput } from '../components/FieldInput'
import { StorageField } from '../components/StorageField'
import { isFieldVisible } from './configVisibility'

export function TaskConfig() {
  const {instance = '', task = ''} = useParams()
  const {schema, t, notify} = useApp()
  const connection = useConnection()
  const navigate = useNavigate()
  const [config, setConfig] = useState<Config>()
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmRun, setConfirmRun] = useState(false)

  const pendingChanges = useRef<Map<string, Value>>(new Map())
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const savingRef = useRef(false)
  const configRef = useRef(config)
  configRef.current = config

  const reload = useCallback(async () => {
    try {
      setConfig(await api.request('config.get', {instance}))
      setError('')
    } catch (error) {
      setError((error as Error).message)
    }
  }, [instance])

  useEffect(() => {
    if (connection === 'ready' && !config) void reload()
  }, [connection, reload, config])

  const flushChanges = useCallback(async () => {
    if (debounceTimer.current) {
      clearTimeout(debounceTimer.current)
      debounceTimer.current = null
    }
    if (!pendingChanges.current.size || savingRef.current || !configRef.current) return
    const currentConfig = configRef.current
    const changes = Array.from(pendingChanges.current.entries()).map(([path, value]) => ({path, value}))
    pendingChanges.current.clear()
    savingRef.current = true

    try {
      const updated = await api.request('config.patch', {
        instance,
        revision: currentConfig.revision,
        changes,
      })
      setConfig(updated)
    } catch (err) {
      notify((err as Error).message, true)
      try {
        const fresh = await api.request('config.get', {instance})
        setConfig(fresh)
      } catch {}
    } finally {
      savingRef.current = false
      if (pendingChanges.current.size) {
        void flushChanges()
      }
    }
  }, [instance, notify])

  const queueChange = useCallback((path: string, value: Value, immediate = false) => {
    pendingChanges.current.set(path, value)

    // 乐观更新本地 config，避免输入卡顿
    setConfig(prev => {
      if (!prev) return prev
      const [tName, gName, aName] = path.split('.')
      return {
        ...prev,
        values: {
          ...prev.values,
          [tName]: {
            ...prev.values[tName],
            [gName]: {
              ...prev.values[tName]?.[gName],
              [aName]: value,
            },
          },
        },
      }
    })

    if (immediate) {
      void flushChanges()
    } else {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
      debounceTimer.current = setTimeout(() => {
        void flushChanges()
      }, 350)
    }
  }, [flushChanges])

  // 组件卸载时刷新未提交的修改
  useEffect(() => {
    return () => {
      if (pendingChanges.current.size) {
        void flushChanges()
      }
    }
  }, [flushChanges])

  async function clearStorage(path: string) {
    if (!config) return
    setBusy(true)
    setError('')
    try {
      const updated = await api.request('config.patch', {instance, revision: config.revision, changes: [{path, value: {}}]})
      setConfig(updated)
      notify('任务内部状态已清除')
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    setBusy(true)
    try {
      await api.request('tasks.run', {instance, task})
      navigate(`/i/${instance}/overview`)
      notify('任务已启动')
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setBusy(false)
      setConfirmRun(false)
    }
  }

  const groups = schema?.args[task]
  const visibleGroups = Object.entries(groups ?? {}).map(([group, fields]) => {
    const visible = Object.entries(fields).filter(([arg, field]) => {
      const value = config?.values[task]?.[group]?.[arg] ?? field.value
      return isFieldVisible(arg, field, value) && `${t(`${group}.${arg}.name`)} ${group}.${arg}`.toLowerCase().includes(search.toLowerCase())
    })
    return {group, visible}
  }).filter(({visible}) => visible.length)

  const tool = Object.values(schema?.menu ?? {}).some(group => group.page === 'tool' && group.tasks.includes(task)) || task === 'FleetScan'

  if (!config) return error ? <ErrorBox message={error} retry={reload} /> : <Loading />

  return (
    <>
      <PageTitle
        title={t(`Task.${task}.name`)}
        actions={tool ? (
          <button className="button secondary" onClick={() => setConfirmRun(true)} disabled={busy || connection !== 'ready'}>
            <Play size={16} />运行工具
          </button>
        ) : undefined}
      />
      {error && <ErrorBox message={error} retry={reload} />}
      <div className="config-toolbar">
        <div className="input-icon">
          <Search size={17} />
          <input placeholder="搜索此任务的配置项…" aria-label="搜索配置项" value={search} onChange={event => setSearch(event.target.value)} />
        </div>
        <span><Settings2 size={14} />{t(`Task.${task}.name`)}</span>
      </div>
      {task === 'FleetInfo' ? (
        <FleetInfo value={config.values.FleetInfo?.FleetInfo?.Result} />
      ) : !groups ? (
        <Empty icon={<Settings2 size={30} />} title="此任务没有独立配置">
          {tool ? '可使用上方按钮运行工具。' : '请在相关任务中查看配置。'}
        </Empty>
      ) : (
        <div className="config-layout">
          <nav className="group-nav">
            {visibleGroups.map(({group}) => (
              <a
                key={group}
                href={`#group-${group}`}
                onClick={event => {
                  event.preventDefault()
                  document.getElementById(`group-${group}`)?.scrollIntoView({behavior: 'smooth', block: 'start'})
                }}
              >
                {t(`${group}._info.name`)}
              </a>
            ))}
          </nav>
          <div className="config-groups">
            {visibleGroups.map(({group, visible}) => (
              <section className="panel config-group" key={group} id={`group-${group}`}>
                <div className="panel-heading">
                  <div>
                    <span className="group-indicator" />
                    <h2>{t(`${group}._info.name`)}</h2>
                  </div>
                  <span className="small-label">{visible.length} 项设置</span>
                </div>
                {visible.map(([arg, field]) => {
                  const path = `${task}.${group}.${arg}`
                  const value = config.values[task]?.[group]?.[arg] ?? field.value
                  const label = t(`${group}.${arg}.name`)
                  const help = t(`${group}.${arg}.help`)
                  const readonly = ['disabled', 'readonly', 'display'].includes(field.display ?? '') || ['storage', 'stored', 'state', 'lock'].includes(field.type)
                  const isMultiline = ['textarea', 'task_priority', 'yaml', 'storage'].includes(field.type) || field.mode === 'yaml'

                  return (
                    <div className={`field-row ${isMultiline ? 'field-row-multiline' : ''}`} key={arg}>
                      <div className="field-label">
                        <label htmlFor={path}>
                          {label}
                          {readonly && <span className="small-label">只读</span>}
                        </label>
                        {help && help !== 'help' && help !== arg && <p>{help.replace(/<[^>]*>/g, '')}</p>}
                      </div>
                      <div className="field-control">
                        {field.type === 'storage' ? (
                          <StorageField value={value} disabled={busy || connection !== 'ready'} onClear={() => void clearStorage(path)} />
                        ) : (
                          <FieldInput
                            id={path}
                            value={value}
                            mode={field.mode}
                            type={field.type === 'input' && typeof field.value === 'number' ? 'number' : field.type}
                            options={field.option}
                            disabled={readonly || busy || connection !== 'ready'}
                            label={label}
                            translateOption={option => t(`${group}.${arg}.${option}`)}
                            onChange={next => {
                              const immediate = ['checkbox', 'select', 'multiselect', 'switch'].includes(field.type)
                              queueChange(path, next, immediate)
                            }}
                          />
                        )}
                      </div>
                    </div>
                  )
                })}
              </section>
            ))}
            {search && !visibleGroups.length && <Empty icon={<Search size={26} />} title="没有找到配置项">试试其他关键词。</Empty>}
          </div>
        </div>
      )}
      {confirmRun && (
        <Modal title={`运行${t(`Task.${task}.name`)}`} onClose={() => setConfirmRun(false)}>
          <p>此操作将连接模拟器并执行该工具。请确认当前实例没有正在运行的任务。</p>
          <button className="button primary" disabled={busy} onClick={run}>
            <Play size={15} />确认运行
          </button>
        </Modal>
      )}
    </>
  )
}

function FleetInfo({value}: {value: unknown}) {
  if (!value || (typeof value === 'object' && !Object.keys(value).length)) return <Empty icon={<Ship size={32}/>} title="还没有舰队扫描记录">在左侧选择舰队扫描，完成扫描后在这里查看。</Empty>
  let fleets: Record<string, Record<string, Array<{name: string; level?: number} | string>>>
  try {fleets = typeof value === 'string' ? JSON.parse(value) : value} catch {return <ErrorBox message="舰队记录格式不正确，请重新扫描"/>}
  return <div className="fleet-grid">{[1, 2, 3, 4, 5, 6].map(fleet => <section className="panel" key={fleet}><div className="panel-heading"><h2>第 {fleet} 舰队</h2><Ship size={18}/></div>{Object.entries({vanguard: '先锋舰队', main: '主力舰队', submarine: '潜艇舰队'}).map(([key, label]) => <div className="fleet-column" key={key}><h3>{label}</h3>{fleets[key]?.[fleet]?.length ? fleets[key][fleet].map((ship, index) => <div key={index}><span>{typeof ship === 'string' ? ship : ship.name}</span><small>{typeof ship !== 'string' && ship.level ? `Lv.${ship.level}` : ''}</small></div>) : <p>暂无记录</p>}</div>)}</section>)}</div>
}

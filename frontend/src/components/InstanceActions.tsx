import { useEffect, useState, useSyncExternalStore } from 'react'
import { useNavigate } from 'react-router-dom'
import { Settings2, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import type { Resource, Status } from '../api/types'
import { useApp, useConnection } from '../app/context'
import { editor } from '../config/editors'
import { EditStatus } from './EditStatus'
import { FieldInput } from './FieldInput'
import { ResourceSettings } from './ResourceCards'
import { ErrorBox, Modal } from './ui'

export function InstanceActions({instance, status, resources, selectedResources, onResourcesChange}: {instance: string; status: Status; resources: Resource[]; selectedResources: string[]; onResourcesChange: (keys: string[]) => void}) {
  const [open, setOpen] = useState(false)
  return <><button className="button" aria-label="实例设置" title="实例设置" onClick={() => setOpen(true)}><Settings2 size={16}/></button>{open && <InstanceSettings instance={instance} status={status} resources={resources} selectedResources={selectedResources} onResourcesChange={onResourcesChange} onClose={() => setOpen(false)}/>}</>
}

function InstanceSettings({instance, status, resources, selectedResources, onResourcesChange, onClose}: {instance: string; status: Status; resources: Resource[]; selectedResources: string[]; onResourcesChange: (keys: string[]) => void; onClose: () => void}) {
  const connection = useConnection()
  const {refresh, notify} = useApp()
  const navigate = useNavigate()
  const queue = editor(`startup:${instance}`)
  const edits = useSyncExternalStore(queue.subscribe, queue.getSnapshot)
  const [startup, setStartup] = useState<boolean>()
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    const confirmed = queue.confirmed()
    void api.request('startup.get', {instance}).then(value => {
      if (active) {setStartup(value.enabled); queue.reconcile(confirmed)}
    }).catch(error => {if (active) setError(error.message)})
    return () => {active = false}
  }, [connection, instance, queue])
  async function remove() {
    setBusy(true); setError('')
    try {
      const config = await api.request('config.get', {instance})
      await api.request('instances.delete', {instance, revision: config.revision})
      await refresh(); onClose(); navigate('/'); notify('实例已移入备份')
    } catch (error) {setError((error as Error).message)} finally {setBusy(false)}
  }
  return <Modal title={instance} onClose={onClose}><div className="form-stack">
    {(error || edits.storageError) && <ErrorBox message={error || edits.storageError}/>}
    <div className="field-row"><label htmlFor="instance-startup">启动时自动运行</label><div><FieldInput id="instance-startup" label="启动时自动运行" value={edits.edits.enabled?.value ?? startup ?? false} disabled={startup === undefined} onChange={value => queue.change('enabled', value)}/><EditStatus id="instance-startup" edit={edits.edits.enabled} retry={queue.retry}/></div></div>
    <ResourceSettings resources={resources} selected={selectedResources} onChange={onResourcesChange}/>
    {deleting && <p>删除 {instance}？配置将保留在备份中。</p>}
    <button className="button danger" disabled={busy || connection !== 'ready' || status === 'running' || status === 'updating'} onClick={() => deleting ? void remove() : setDeleting(true)}><Trash2 size={15}/>{deleting ? '确认删除' : '删除实例'}</button>
  </div></Modal>
}

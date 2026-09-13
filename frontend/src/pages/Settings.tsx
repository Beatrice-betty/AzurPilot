import { useEffect, useState, useSyncExternalStore } from 'react'
import { useParams } from 'react-router-dom'
import { Palette, Settings2, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import type { Settings as SettingsData } from '../api/types'
import { languages, useApp, useConnection } from '../app/context'
import { ErrorBox, Loading, Modal, PageTitle } from '../components/ui'
import { editor, prepareValue } from '../config/editors'
import { EditStatus } from '../components/EditStatus'
import { FieldInput } from '../components/FieldInput'

export function Settings() {
  const {instance = ''} = useParams()
  const {notify, refresh, instances, theme, setTheme, language, setLanguage, t} = useApp()
  const [data, setData] = useState<SettingsData>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [startup, setStartup] = useState(false)
  const connection = useConnection()

  const deployQueue = editor('deploy')
  const startupQueue = editor(`startup:${instance}`)
  const deployEdits = useSyncExternalStore(deployQueue.subscribe, deployQueue.getSnapshot)
  const startupEdits = useSyncExternalStore(startupQueue.subscribe, startupQueue.getSnapshot)
  const startupValue = startupEdits.edits.enabled?.value ?? startup

  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    const confirmedDeploy = deployQueue.confirmed()
    const confirmedStartup = startupQueue.confirmed()
    void Promise.all([api.request('settings.get', {}), api.request('startup.get', {instance})])
      .then(([data, start]) => {
        if (active) {
          setData(data)
          setStartup(start.enabled)
          deployQueue.reconcile(confirmedDeploy)
          startupQueue.reconcile(confirmedStartup)
        }
      })
      .catch(error => {
        if (active) setError(error.message)
      })
    return () => { active = false }
  }, [connection, instance, deployQueue, startupQueue])

  async function remove() {
    setBusy(true)
    try {
      const config = await api.request('config.get', {instance})
      await api.request('instances.delete', {instance, revision: config.revision})
      await refresh()
      notify('实例已移入配置备份目录')
      setDeleting(false)
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageTitle title="系统设置" />
      {error && <ErrorBox message={error} />}
      {(deployEdits.storageError || startupEdits.storageError) && <ErrorBox message={deployEdits.storageError || startupEdits.storageError} />}
      <section className="panel config-group">
        <div className="panel-heading">
          <div>
            <Palette size={18} />
            <h2>界面偏好</h2>
          </div>
        </div>
        <div className="field-row">
          <div className="field-label">
            <label htmlFor="ui-theme">界面主题</label>
            <p>即时切换当前浏览器的外观。</p>
          </div>
          <div className="field-control">
            <select id="ui-theme" value={theme} onChange={event => setTheme(event.target.value as typeof theme)}>
              <option value="light">浅色</option>
              <option value="dark">深色</option>
            </select>
          </div>
        </div>
        <div className="field-row">
          <div className="field-label">
            <label htmlFor="ui-language">界面语言</label>
            <p>切换任务菜单、配置名称及说明的语言，选择会保存在当前浏览器中。</p>
          </div>
          <div className="field-control">
            <select id="ui-language" value={language} disabled={connection !== 'ready'} onChange={event => setLanguage(event.target.value as typeof language)}>
              {Object.entries(languages).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </div>
        </div>
      </section>
      <section className="panel instance-settings">
        <div className="panel-heading">
          <div>
            <Settings2 size={18} />
            <h2>实例管理</h2>
          </div>
        </div>
        <div className="field-row">
          <div className="field-label">
            <label>启动服务时自动运行 {instance}</label>
            <p>服务启动后自动接续此实例的任务调度。</p>
          </div>
          <div className="field-control"><FieldInput id="startup" value={startupValue} onChange={value => startupQueue.change('enabled', value)} label="启动时自动运行" />
            <EditStatus id="startup" edit={startupEdits.edits.enabled} retry={startupQueue.retry} /></div>
        </div>
        <div className="field-row">
          <div className="field-label">
            <label>删除当前实例</label>
            <p>配置将移入备份目录；必须先停止运行中的实例。</p>
          </div>
          <button
            className="button danger subtle"
            disabled={busy || connection !== 'ready' || instances.find(item => item.name === instance)?.status === 'running'}
            onClick={() => setDeleting(true)}
          >
            <Trash2 size={15} />删除实例
          </button>
        </div>
      </section>
      <p className="settings-notice">以下为服务端部署设置，修改后实时保存，重启服务后生效。界面偏好即时生效。</p>
      {!data ? (
        <Loading />
      ) : (
        data.groups.map(group => (
          <section className="panel config-group" key={group.key}>
            <div className="panel-heading">
              <h2>{t(`Gui.DeploySetting.Group${group.key}`)}</h2>
            </div>
            {group.fields
              .filter(field => !['CDN', 'DpiScaling', 'Theme', 'Language'].includes(field.key))
              .map(field => (
                <div className={`field-row ${['textarea', 'yaml', 'task_priority'].includes(field.type) ? 'field-row-multiline' : ''}`} key={field.key}>
                  <div className="field-label">
                    <label htmlFor={`deploy-${field.key}`}>{field.label}</label>
                    <p>{field.key === 'Password' ? '留空保留原密码。新密码在重启服务后生效。' : field.help.replace(/<[^>]*>/g, '')}</p>
                  </div>
                  <div className="field-control">
                    <FieldInput
                      id={`deploy-${field.key}`}
                      label={field.label}
                      type={field.type}
                      options={field.options}
                      value={deployEdits.edits[field.key]?.value ?? field.value}
                      preserveText
                      invalid={deployEdits.edits[field.key]?.status === 'error'}
                      disabled={data.demo}
                      onChange={value => {
                        const {payload, error} = prepareValue(value, field)
                        deployQueue.change(field.key, value, payload, error)
                      }}
                    />
                    <EditStatus id={`deploy-${field.key}`} edit={deployEdits.edits[field.key]} retry={deployQueue.retry} />
                  </div>
                </div>
              ))}
          </section>
        ))
      )}
      {deleting && (
        <Modal title={`删除实例 ${instance}`} onClose={() => setDeleting(false)}>
          <p>实例将从列表移除，原配置保留在 config/backup 中。</p>
          <button className="button danger" disabled={busy} onClick={remove}>确认删除</button>
        </Modal>
      )}
    </>
  )
}

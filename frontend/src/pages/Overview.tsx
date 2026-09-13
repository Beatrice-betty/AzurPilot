import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CalendarClock, ChevronRight, Clock3, Play, Square } from 'lucide-react'
import { api } from '../api/client'
import type { Overview as OverviewData } from '../api/types'
import { useApp, useConnection } from '../app/context'
import { Empty, ErrorBox, Loading, PageTitle } from '../components/ui'
import { MonitorPanel } from '../components/MonitorPanel'
import { ResourceCards } from '../components/ResourceCards'
import { editor } from '../config/editors'

export function Overview() {
  const {instance = ''} = useParams()
  const [data, setData] = useState<OverviewData>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const connection = useConnection()
  const {notify} = useApp()
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    void api.request('overview.get', {instance}).then(value => { if (active) {setData(value); setError('')} }).catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [instance, connection])
  useEffect(() => api.onEvent(event => { if (event.topic === 'overview' && (event.data as OverviewData).instance === instance) setData(event.data as OverviewData) }), [instance])
  async function toggle() {
    if (!data) return
    setBusy(true)
    try {
      if (data.status !== 'running') await editor(`config:${instance}`).settled()
      setData(await api.request(data.status === 'running' ? 'scheduler.stop' : 'scheduler.start', {instance}))
      notify(data.status === 'running' ? '调度器已停止' : '调度器已启动')
    }
    catch (error) { notify((error as Error).message, true) } finally { setBusy(false) }
  }
  if (error) return <ErrorBox message={error}/>
  if (!data) return <Loading/>
  const pending = data.tasks.filter(task => task.state === 'pending').length
  const running = data.tasks.filter(task => task.state === 'running').length
  return <>
    <PageTitle title="运行总览" actions={<button className={`button ${data.status === 'running' ? 'danger' : 'primary'}`} onClick={toggle} disabled={busy || connection !== 'ready'}>{data.status === 'running' ? <Square size={15}/> : <Play size={15}/>} {busy ? '正在处理…' : data.status === 'running' ? '停止运行' : '启动调度器'}</button>}/>
    <ResourceCards instance={instance} resources={data.resources}/>
    <div className="overview-grid"><section className="panel schedule-panel"><div className="panel-heading"><div><CalendarClock size={18}/><h2>任务计划</h2><span className="count-badge">{data.tasks.length}</span></div><span className="small-label">自动同步</span></div><div className="schedule-summary"><div><span className="tiny-dot teal"/>运行中 <strong>{running}</strong></div><div>待执行 <strong>{pending}</strong></div><div><Clock3 size={13}/>等待中 <strong>{data.tasks.length - pending - running}</strong></div><span>下次运行时间</span></div><div className="task-table">{data.tasks.length ? data.tasks.map((task, index) => <Link className="task-row" key={task.name} to={`/i/${instance}/task/${task.name}`}><span className="task-order">{String(index + 1).padStart(2, '0')}</span><div className="task-row-name"><strong>{task.label}</strong></div><span className={`task-state ${task.state}`}>{{running: '运行中', pending: '待执行', waiting: '等待中'}[task.state]}</span><time>{task.state === 'running' ? '正在执行' : task.pending ? '等待调度' : task.nextRun.slice(5, 16)}</time><ChevronRight size={14}/></Link>) : <Empty icon={<CalendarClock size={30}/>} title="还没有启用的任务">从左侧任务配置中启用日常任务。</Empty>}</div></section>
      <MonitorPanel instance={instance}/>
    </div>
  </>
}

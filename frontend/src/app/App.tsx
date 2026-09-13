import { useEffect, useState, type FormEvent } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ArrowRight, ChartNoAxesCombined, Compass, LayoutDashboard, Menu, Plus, Settings2, Wifi, WifiOff, X } from 'lucide-react'
import { api } from '../api/client'
import { useApp, useConnection } from './context'
import { ErrorBox, Loading, Modal } from '../components/ui'
import { InstanceSwitcher } from '../components/InstanceSwitcher'
import { TaskNav } from '../components/TaskNav'

export function CreateInstance({onClose}: {onClose: () => void}) {
  const [name, setName] = useState('')
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const {instances, refresh, notify} = useApp()
  const navigate = useNavigate()
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      await api.request('instances.create', {name, source: source || null})
      await refresh(); onClose(); notify('实例已创建，请设置模拟器连接')
      navigate(`/i/${name}/task/Alas`)
    } catch (error) { setError((error as Error).message) } finally { setBusy(false) }
  }
  return <Modal title="创建配置实例" onClose={onClose}><form onSubmit={submit} className="form-stack">
    <p className="muted">每个实例独立保存任务计划与模拟器连接。</p>
    <label>实例名称<input autoFocus required pattern="[A-Za-z][A-Za-z0-9_-]{0,63}" value={name} onChange={event => setName(event.target.value)} placeholder="例如：alas-main" maxLength={64}/></label>
    <label>初始配置<select value={source} onChange={event => setSource(event.target.value)}><option value="">使用默认配置</option>{instances.map(item => <option key={item.name}>{item.name}</option>)}</select></label>
    {error && <ErrorBox message={error}/>}
    <button className="button primary" disabled={busy}>{busy ? '正在创建…' : '创建实例'}<ArrowRight size={16}/></button>
  </form></Modal>
}

function Login() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setBusy(true)
    try { await api.login(password) } catch (error) { setError((error as Error).message) } finally { setBusy(false) }
  }
  return <div className="login-page"><div className="login-art"><Compass size={200} strokeWidth={0.5}/><span>让每一次出航，都井然有序。</span></div>
    <form onSubmit={submit} className="login-card"><div className="brand-mark"><NavigationMark/></div><h1>欢迎回到指挥室</h1>
      <label htmlFor="password">访问密码</label><input id="password" type="password" autoComplete="current-password" autoFocus required value={password} onChange={event => setPassword(event.target.value)}/>
      {error && <ErrorBox message={error}/>}
      <button className="button primary" disabled={busy}>{busy ? '正在验证…' : '进入控制台'}<ArrowRight size={16}/></button>
      <small>自动生成的密码保存在服务端 password.txt 中。</small>
    </form></div>
}

export function NavigationMark() {
  return <svg width="28" height="28" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M16 3 28 28 16 22 4 28 16 3Z" fill="currentColor"/><path d="m16 12 4 9-4-2-4 2 4-9Z" fill="#101e2c"/></svg>
}

export function App() {
  const connection = useConnection()
  const {instances, schema, t, notify, previewEnabled} = useApp()
  const {instance} = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [creating, setCreating] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const current = instances.find(item => item.name === instance)
  const base = instance ? `/i/${instance}` : ''
  const taskMatch = location.pathname.match(/\/task\/([^/]+)/)
  const currentTask = taskMatch ? taskMatch[1] : null
  const activeSection = location.pathname.includes('/task/') ? '任务配置' : location.pathname.endsWith('/statistics') ? '资源统计' : location.pathname.endsWith('/settings') ? '系统设置' : location.pathname.endsWith('/logs') ? '运行日志' : '运行总览'
  useEffect(() => {
    if (connection === 'ready' && instances.length && (!instance || !instances.some(item => item.name === instance))) {
      navigate(`/i/${instances[0].name}/overview`, {replace: true})
    }
  }, [connection, instances, instance, navigate])
  useEffect(() => { setMobileOpen(false) }, [location.pathname])
  useEffect(() => {
    if (connection !== 'ready') return
    void api.request('events.subscribe', {instance: instance ?? null, topics: instance ? previewEnabled ? ['instances', 'overview', 'logs', 'preview'] : ['instances', 'overview', 'logs'] : ['instances']}).catch(error => notify(error.message, true))
  }, [instance, connection, notify, previewEnabled])
  if (connection === 'auth') return <Login/>
  return <div className={`app-shell ${mobileOpen ? 'mobile-open' : ''}`}>
    <aside className="sidebar"><div className="sidebar-brand"><div className="brand-title"><img src="/azurpilot.svg" alt="AzurPilot" className="brand-logo"/><span>AzurPilot</span></div><button className="mobile-close icon-button" aria-label="关闭导航" onClick={() => setMobileOpen(false)}><X size={18}/></button></div>
      <InstanceSwitcher onCreate={() => setCreating(true)}/>
      <div className="sidebar-label">工作空间</div>
      <nav className="primary-nav"><NavLink to={`${base}/overview`}><LayoutDashboard size={17}/>运行总览<span className="nav-pill">总览</span></NavLink><NavLink to={`${base}/statistics`}><ChartNoAxesCombined size={17}/>资源统计</NavLink><NavLink to={`${base}/settings`}><Settings2 size={17}/>系统设置</NavLink></nav>
      <div className="sidebar-label">任务配置 <span>{schema ? Object.values(schema.menu).flatMap(group => group.tasks).length : '—'}</span></div>
      <TaskNav/>
      <div className="sidebar-footer"><span className={`connection-dot ${connection === 'ready' ? 'online' : ''}`}/><span>{connection === 'ready' ? '服务连接正常' : '等待服务连接'}</span></div>
    </aside>
    <div className="main-shell"><header className="topbar"><button className="mobile-toggle icon-button" aria-label="打开导航" onClick={() => setMobileOpen(true)}><Menu size={20}/></button><div className="breadcrumb"><Link to={base ? `${base}/overview` : '/'}>工作空间</Link><span>/</span><Link to={base ? `${base}/overview` : '/'}>{instance ?? '欢迎'}</Link>{currentTask ? <><span>/</span><Link to={`${base}/task/Alas`}>任务配置</Link><span>/</span><Link to={`${base}/task/${currentTask}`}><strong>{t(`Task.${currentTask}.name`)}</strong></Link></> : <><span>/</span><Link to={location.pathname}><strong>{activeSection}</strong></Link></>}</div><div className="topbar-right"><span className="connection-label">{connection === 'ready' ? <Wifi size={14}/> : <WifiOff size={14}/>}{connection === 'ready' ? '已连接' : '连接中'}</span><span className="topbar-divider"/><span className="version">控制台 / v1</span></div></header>
      {connection !== 'ready' && <div className="connection-banner" role="status"><WifiOff size={16}/>正在连接后端，配置输入会保留并在重连后保存；运行操作暂不可用。</div>}
      <main>{!schema ? <Loading/> : !instances.length ? <div className="welcome"><Compass size={84} strokeWidth={1}/><h1>从一个新实例开始</h1><button className="button primary" onClick={() => setCreating(true)}><Plus size={17}/>创建第一个实例</button></div> : current ? <Outlet key={instance}/> : <Loading/>}</main>
    </div>{creating && <CreateInstance onClose={() => setCreating(false)}/>}
  </div>
}

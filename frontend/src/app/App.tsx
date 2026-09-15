import { PasswordInput, Select } from '../components/FormControls'
import { useEffect, useState, type FormEvent, type MouseEvent } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ArrowRight, CalendarClock, ChartNoAxesCombined, Code2, Compass, LayoutDashboard, House, Download, Menu, Settings2, WifiOff, X } from 'lucide-react'
import { api } from '../api/client'
import { useApp, useConnection } from './context'
import { ErrorBox, Loading, Modal } from '../components/ui'
import { GlassMaterial } from '../components/GlassMaterial'
import { InstanceSwitcher } from '../components/InstanceSwitcher'
import { RightRail } from '../components/RightRail'
import { TaskNav } from '../components/TaskNav'
import { useUpdater } from './updater'
import { recordDevLogoClick } from './devMode'

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
    <label>初始配置<Select value={source} onChange={event => setSource(event.target.value)}><option value="">使用默认配置</option>{instances.map(item => <option key={item.name}>{item.name}</option>)}</Select></label>
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
      <label htmlFor="password">访问密码</label><PasswordInput id="password" autoComplete="current-password" autoFocus required value={password} onChange={event => setPassword(event.target.value)}/>
      {error && <ErrorBox message={error}/>}
      <button className="button primary" disabled={busy}>{busy ? '正在验证…' : '进入控制台'}<ArrowRight size={16}/></button>
      <small>自动生成的密码保存在服务端 password.txt 中。</small>
    </form></div>
}

export function NavigationMark() {
  return <img src="/azurpilot.svg" alt="AzurPilot" width="28" height="28" className="brand-logo"/>
}

export function App() {
  const connection = useConnection()
  const {instancesLoaded, instances, schema, t, notify, previewEnabled, devMode, setDevMode} = useApp()
  const {instance} = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [creating, setCreating] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [railOpen, setRailOpen] = useState(false)
  const update = useUpdater()
  const current = instances.find(item => item.name === instance)
  const base = instance ? `/i/${instance}` : ''
  const taskMatch = location.pathname.match(/\/task\/([^/]+)/)
  const currentTask = taskMatch ? taskMatch[1] : null
  const activeSection = location.pathname.includes('/task/') ? '任务配置' : location.pathname.endsWith('/statistics') ? '资源统计' : location.pathname.endsWith('/settings') ? '系统设置' : location.pathname.endsWith('/updater') ? '更新器' : location.pathname.endsWith('/dev') ? '开发者' : instance ? instance : '主页'
  function handleBrandLogoClick(event: MouseEvent<HTMLImageElement>) {
    if (devMode || !recordDevLogoClick()) return
    event.preventDefault()
    setDevMode(true)
    notify('开发者模式已开启')
    navigate('/dev')
  }
  useEffect(() => {
    if (connection === 'ready' && instancesLoaded && instance && !instances.some(item => item.name === instance)) {
      navigate('/', {replace: true})
    }
  }, [connection, instances, instance, navigate, instancesLoaded])
  useEffect(() => { setMobileOpen(false); setRailOpen(false) }, [location.pathname])
  useEffect(() => {
    if (connection !== 'ready') return
    void api.request('events.subscribe', {instance: instance ?? null, topics: instance ? previewEnabled ? ['instances', 'overview', 'logs', 'preview'] : ['instances', 'overview', 'logs'] : ['instances']}).catch(error => notify(error.message, true))
  }, [instance, connection, notify, previewEnabled])
  if (connection === 'auth') return <Login/>
  return <div className={`app-shell ${instance ? 'with-rail' : ''} ${mobileOpen ? 'mobile-open' : ''} ${railOpen ? 'rail-open' : ''}`}>
    <a className="skip-link" href="#main-content" onClick={event => {event.preventDefault(); document.getElementById('main-content')?.focus()}}>跳转到内容</a><aside className="sidebar"><div className="sidebar-brand"><div className="sidebar-brand-left"><Link to="/" className="brand-title" aria-label="AzurPilot 主页"><img src="/azurpilot.svg" alt="" className="brand-logo" onClick={handleBrandLogoClick}/><span>AzurPilot</span></Link>{update.data?.available && <Link className="update-notice sidebar-update-notice" to="/updater" aria-label="新版本可用" title="新版本可用"><span>New</span></Link>}</div><button className="mobile-close icon-button" aria-label="关闭导航" onClick={() => setMobileOpen(false)}><X size={18}/></button></div>
      <nav className="primary-nav" aria-label="主导航">
        {instance ? <><NavLink to={`${base}/overview`}><LayoutDashboard size={17}/>运行总览</NavLink><NavLink to={`${base}/statistics`}><ChartNoAxesCombined size={17}/>资源统计</NavLink></> : <><NavLink to="/" end><House size={17}/>主页</NavLink><NavLink to="/updater"><Download size={17}/>更新器{update.data?.available && <span className="tiny-dot teal"/>}</NavLink><NavLink to="/settings"><Settings2 size={17}/>系统设置</NavLink>{devMode && <NavLink to="/dev"><Code2 size={17}/>开发者</NavLink>}</>}
      </nav>
      {instance && <TaskNav/>}
    </aside>
    <div className="main-shell"><header className="topbar"><GlassMaterial/><button className="mobile-toggle icon-button" aria-label="打开导航" onClick={() => setMobileOpen(true)}><Menu size={20}/></button>
      <div className="breadcrumb"><Link to="/">主页</Link>{instance ? <><span>/</span><InstanceSwitcher onCreate={() => setCreating(true)}/>{currentTask ? <><span>/</span><Link to={`${base}/task/Alas`}>任务配置</Link><span>/</span><Link className="breadcrumb-current" to={`${base}/task/${currentTask}`}><strong>{t(`Task.${currentTask}.name`)}</strong></Link></> : location.pathname.endsWith('/statistics') && <><span>/</span><strong>资源统计</strong></>}</> : activeSection !== '主页' && <><span>/</span><strong>{activeSection}</strong></>}</div>
      <div className="topbar-right">{instance && <button className="mobile-rail-toggle icon-button" aria-label={railOpen ? '关闭调度与任务' : '打开调度与任务'} aria-expanded={railOpen} aria-controls="right-rail-menu" onClick={() => setRailOpen(open => !open)}><CalendarClock size={18}/></button>}</div></header>
      {connection !== 'ready' && <div className="connection-banner" role="status"><WifiOff size={16}/>正在连接后端，配置输入会保留并在重连后保存；运行操作暂不可用。</div>}
      <main id="main-content" tabIndex={-1}>{!schema || ((instance || location.pathname === '/') && !instancesLoaded) ? <Loading/> : !instance || current ? <Outlet context={update} key={instance ?? 'home'}/> : <Loading/>}</main>
    </div>
    {instance && <RightRail instance={instance} onMobileClose={() => setRailOpen(false)}/>}
    {creating && <CreateInstance onClose={() => setCreating(false)}/>}
  </div>
}

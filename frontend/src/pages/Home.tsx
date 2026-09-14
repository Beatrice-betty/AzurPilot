import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Activity, CircleAlert, Layers3, Plus, Server } from 'lucide-react'
import { useApp, useConnection } from '../app/context'
import { CreateInstance } from '../app/App'
import { PageTitle, StatusBadge } from '../components/ui'

function getGreeting(): string {
  const hour = new Date().getHours()
  if (hour >= 5 && hour < 12) return '上午好，指挥官！'
  if (hour >= 12 && hour < 18) return '下午好，指挥官！'
  return '晚上好，指挥官！'
}

export function Home() {
  const {instances, t} = useApp()
  const connection = useConnection()
  const [creating, setCreating] = useState(false)
  return <>
    <div className="home-intro"><span className="eyebrow">你的指挥中心</span><PageTitle title={getGreeting()}/><p>每一次出航，都井然有序。所有实例与任务，尽在掌握。</p></div>
    <div className="home-summary" aria-label="实例状态摘要">
      <div><Layers3 size={19}/><span>全部实例</span><strong>{instances.length}</strong></div>
      <div><Activity size={19}/><span>运行中</span><strong>{instances.filter(item => item.status === 'running').length}</strong></div>
      <div><CircleAlert size={19}/><span>需要处理</span><strong>{instances.filter(item => item.status === 'error').length}</strong></div>
    </div>
    <section className="home-instances">
      <div className="home-section-heading"><h2>实例 <span className="count-badge">{instances.length}</span></h2><button className="button primary" disabled={connection !== 'ready'} onClick={() => setCreating(true)}><Plus size={16}/>新建实例</button></div>
      <div className="instance-grid">
        {instances.map(item => <Link className="instance-card panel" key={item.name} to={`/i/${item.name}/overview`}>
          <div className="instance-card-heading"><span className="home-instance-icon"><Server size={22}/></span><StatusBadge status={item.status}/></div>
          <h3>{item.name}</h3><div className="instance-device">{item.server !== 'disabled' && <span>{t(`Emulator.ServerName.${item.server}`)}</span>}<span>{item.serial}</span></div>
          <div className="instance-card-footer"><span>{item.status === 'running' ? item.currentTask ? t(`Task.${item.currentTask}.name`) : '等待调度' : item.status === 'error' ? '需要处理' : item.status === 'updating' ? '正在更新' : '未运行'}</span><ArrowRight size={17}/></div>
        </Link>)}
        {!instances.length && <button className="instance-card instance-add" disabled={connection !== 'ready'} onClick={() => setCreating(true)}><Plus size={32}/><span>创建第一个实例</span></button>}
      </div>
    </section>
    {creating && <CreateInstance onClose={() => setCreating(false)}/>}
  </>
}

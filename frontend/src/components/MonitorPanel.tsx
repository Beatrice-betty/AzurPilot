import { useEffect, useState } from 'react'
import { Download, Image, Terminal } from 'lucide-react'
import { api } from '../api/client'
import type { Preview } from '../api/types'
import { useApp } from '../app/context'
import { Empty } from './ui'
import { LogPanel } from './LogPanel'

export function MonitorPanel({instance}: {instance: string}) {
  const [view, setView] = useState('logs')
  const [frame, setFrame] = useState<Preview>()
  const {setPreviewEnabled} = useApp()
  useEffect(() => {
    setPreviewEnabled(view === 'preview')
    return () => setPreviewEnabled(false)
  }, [view, setPreviewEnabled])
  useEffect(() => api.onEvent(event => {
    if (event.topic === 'preview' && (event.data as Preview).instance === instance) setFrame(event.data as Preview)
  }), [instance])
  return <section className="panel monitor-panel"><div className="monitor-tabs" aria-label="运行监控">
    <div className={`monitor-segmented ${view}`} role="tablist" aria-label="监控视图">
      <button role="tab" aria-selected={view === 'logs'} onClick={() => setView('logs')}><Terminal size={15}/>日志</button>
      <button role="tab" aria-selected={view === 'preview'} onClick={() => setView('preview')}><Image size={15}/>截图</button>
    </div>
    {view === 'preview' && frame?.image && <a className="text-button" href={frame.image} download={`${instance}-screenshot.jpg`}><Download size={14}/>保存截图</a>}
  </div>
    <div className="monitor-view" hidden={view !== 'logs'}><LogPanel active={view === 'logs'}/></div>
    <div className="monitor-view" hidden={view !== 'preview'}><div className="preview-screen">{frame?.image ? <img src={frame.image} alt="任务最近一次截图"/> : <Empty icon={<Image size={42}/>} title="等待任务截图">任务截图后自动更新；空闲时保留最后画面。</Empty>}</div></div>
  </section>
}

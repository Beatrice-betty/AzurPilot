import { useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { ArrowDown, ArrowUp, Check, CircleAlert, Download, GitBranch, GitCommitHorizontal, RefreshCw, X } from 'lucide-react'
import { api } from '../api/client'
import type { CommitHistory } from '../api/types'
import type { UpdaterState } from '../app/updater'
import { useConnection } from '../app/context'
import { Empty, ErrorBox, Loading, PageTitle } from '../components/ui'

const states: Record<string, string> = {idle: '已是最新', available: '新版本可用', fetch: '正在获取更新', checking: '正在检查更新', apply: '正在更新', start: '准备更新', wait: '等待任务结束', 'run update': '正在更新', reload: '正在重启', failed: '更新失败', finish: '更新完成', cancel: '正在取消'}

export function Updater() {
  const {data, error: statusError, refresh} = useOutletContext<UpdaterState>()
  const connection = useConnection()
  const [history, setHistory] = useState<CommitHistory>()
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const local = data?.localHead, upstream = data?.upstreamHead
  useEffect(() => {setOffset(0)}, [local, upstream])
  useEffect(() => {
    if (connection !== 'ready' || local === undefined) return
    let active = true
    setLoading(true); setError('')
    void api.request('updater.commits', {offset, limit: 50}).then(value => {
      if (active) setHistory(value)
    }).catch(error => {if (active) setError(error.message)}).finally(() => {if (active) setLoading(false)})
    return () => {active = false}
  }, [connection, local, upstream, offset, retry])
  async function act(method: 'updater.fetch' | 'updater.apply' | 'updater.cancel') {
    setBusy(true); setError('')
    try {await api.request(method, {}); refresh()}
    catch (error) {setError((error as Error).message)} finally {setBusy(false)}
  }
  const disabled = busy || connection !== 'ready' || !data || data.busy
  const statusLabel = !local || !upstream ? '未获取版本信息' : data?.state === 'failed' ? '更新失败' : data?.available && !data.busy ? '新版本可用' : states[data?.state ?? ''] ?? data?.state
  return <>
    <PageTitle title="更新器" actions={<><button className="button" disabled={disabled} onClick={() => void act('updater.fetch')}><RefreshCw size={16} className={data?.state === 'fetch' || data?.state === 'checking' ? 'spin' : ''}/>获取更新</button><button className="button primary" disabled={disabled || !data?.canApply} onClick={() => void act('updater.apply')}><Download size={16}/>更新</button>{data?.canCancel && <button className="button" disabled={busy || connection !== 'ready'} onClick={() => void act('updater.cancel')}><X size={16}/>取消更新</button>}</>}/>
    {(error || statusError || data?.error) && <ErrorBox message={error || statusError || data?.error || ''} retry={() => {refresh(); setRetry(value => value + 1)}}/>}
    {!data ? <Loading/> : <>
      <div className="update-summary"><span><GitBranch size={16}/>{data.branch}</span><span className={data.available ? 'update-available' : ''}>{data.busy ? <RefreshCw size={15} className="spin"/> : data.state === 'failed' || !upstream ? <CircleAlert size={15}/> : <Check size={15}/>} {statusLabel}</span>{data.ahead > 0 && <span title="本地领先"><ArrowUp size={14}/>{data.ahead}</span>}{data.behind > 0 && <span title="本地落后"><ArrowDown size={14}/>{data.behind}</span>}</div>
      <div className="head-grid">{[['本地 HEAD', local], ['上游 HEAD', upstream]].map(([label, sha]) => <div className="panel head-card" key={label}><span><GitCommitHorizontal size={18}/>{label}</span><code title={sha ?? ''}>{sha ?? '未获取'}</code></div>)}</div>
      {data.ahead > 0 && <p className="muted">本地领先 {data.ahead} 个提交</p>}
      <section className="panel commit-panel"><div className="panel-heading"><div><GitCommitHorizontal size={18}/><h2>提交记录</h2><span className="count-badge">{history?.total ?? '—'}</span></div></div>
        {loading ? <Loading/> : history?.entries.length ? <div className="commit-list">{history.entries.map(commit => <article className="commit-row" key={commit.sha}>
          <GitCommitHorizontal className="commit-node" size={18}/><div className="commit-detail"><div className="commit-subject">{commit.message.split('\n')[0]}{commit.sha === local && <span className="commit-ref">本地 HEAD</span>}{commit.sha === upstream && <span className="commit-ref upstream">上游 HEAD</span>}</div>
          {commit.message.includes('\n') && <details><summary>展开详情</summary><pre>{commit.message.slice(commit.message.indexOf('\n')).trim()}</pre></details>}
          <div className="commit-meta"><code title={commit.sha}>{commit.sha.slice(0, 10)}</code><span>{commit.author}</span><time dateTime={commit.date}>{new Date(commit.date).toLocaleString()}</time></div></div>
        </article>)}</div> : <Empty title="暂无提交记录"/>}
        <div className="commit-pagination"><span>{history?.total ? `${offset + 1}–${Math.min(offset + 50, history.total)} / ${history.total}` : '0'}</span><button className="button" disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 50))}>上一页</button><button className="button" disabled={loading || !history?.hasMore} onClick={() => setOffset(value => value + 50)}>下一页</button></div>
      </section>
    </>}
  </>
}

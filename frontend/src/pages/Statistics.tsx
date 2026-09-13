import { lazy, Suspense, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Download, RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import type { StatisticsReport } from '../api/types'
import type { Parameters } from '../api/generated'
import { useConnection } from '../app/context'
import { ErrorBox, Loading, PageTitle } from '../components/ui'
import { StatisticsTable } from '../components/StatisticsTable'
import { downloadCsv } from '../components/statisticsData'

const StatisticsChart = lazy(() => import('../components/StatisticsChart').then(module => ({default: module.StatisticsChart})))
const categories = {resources: '资源趋势', action: '大世界趋势', opsi: '大世界总结', commission: '委托收益', ships: '舰船经验', loot: '短猫掉落'}
type Category = Parameters['statistics.report']['category']

export function Statistics() {
  const {instance = ''} = useParams()
  const [category, setCategory] = useState<Category>('resources')
  const [days, setDays] = useState(7)
  const [month, setMonth] = useState(() => {const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`})
  const [period, setPeriod] = useState<'day' | 'week' | 'month'>('month')
  const [revision, setRevision] = useState(0)
  const [data, setData] = useState<StatisticsReport>()
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const connection = useConnection()
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    setData(undefined); setError('')
    void api.request('statistics.report', {instance, category, days, month, period}).then(value => {if (active) setData(value)}).catch(error => {if (active) setError(error.message)})
    return () => {active = false}
  }, [instance, category, days, month, period, connection, revision])
  async function refresh() {
    setRefreshing(true)
    try {
      if (category === 'loot') await api.request('statistics.refreshLoot', {instance})
      setRevision(value => value + 1)
    } catch (error) {setError((error as Error).message)} finally {setRefreshing(false)}
  }
  function download() {
    if (!data) return
    downloadCsv(`${instance}-${categories[category!]}-${data.month}`, [
      ['指标', '数值', '单位'], ...data.metrics.map(item => [item.label, item.value, item.unit]),
      ...data.tables.flatMap(table => [[table.title], table.columns, ...table.rows, []]),
      ...data.series.flatMap(series => [[series.label], ['时间', '数值', '来源'], ...series.points.map(point => [point.time, point.value, point.source ?? '']), []]),
      ...(data.notes.length ? [['统计说明'], ...data.notes.map(note => [note])] : []),
    ])
  }
  return <><PageTitle title="资源统计" actions={<><button className="button secondary" disabled={connection !== 'ready' || refreshing} onClick={refresh}><RefreshCw size={15}/>{refreshing ? '正在刷新…' : '刷新统计'}</button><button className="button secondary" disabled={!data} onClick={download}><Download size={15}/>导出本类数据</button></>}/>
    <nav className="statistics-tabs" aria-label="统计分类">{Object.entries(categories).map(([key, label]) => <button aria-current={category === key ? 'page' : undefined} className={category === key ? 'active' : ''} key={key} onClick={() => setCategory(key as Category)}>{label}</button>)}</nav>
    <div className="statistics-controls period-controls"><strong>{categories[category!]}</strong>{category === 'resources' ? <label>时间范围<select aria-label="统计天数" value={days} onChange={event => setDays(Number(event.target.value))}>{[1, 7, 30, 90, 365].map(value => <option value={value} key={value}>最近 {value} 天</option>)}</select></label> : ['action', 'opsi', 'commission'].includes(category!) && <label>统计月份<input aria-label="统计月份" type="month" min="2020-01" max="9998-12" value={month} disabled={category === 'commission' && period !== 'month'} onChange={event => {if (event.target.value) setMonth(event.target.value)}}/></label>}{category === 'commission' && <label>汇总周期<select aria-label="委托汇总周期" value={period} onChange={event => setPeriod(event.target.value as typeof period)}><option value="day">今日</option><option value="week">本周</option><option value="month">选定月份</option></select></label>}{category === 'ships' && <span>最新检测与历史日记录</span>}{category === 'loot' && <span>本设备全部历史 · 跨实例累计</span>}</div>
    {error ? <ErrorBox message={error} retry={() => setRevision(value => value + 1)}/> : !data ? <Loading/> : <div className="statistics-sections">{!!data.metrics.length && <div className="stat-metrics summary-metrics">{data.metrics.map(item => <section key={item.label}><span>{item.label}</span><strong>{item.value == null ? '—' : item.value.toLocaleString(undefined, {maximumFractionDigits: 2})}<small>{item.unit}</small></strong></section>)}</div>}{!!data.series.length && <Suspense fallback={<Loading/>}><StatisticsChart key={category} series={data.series}/></Suspense>}{data.tables.map(table => <section className="panel" key={table.title}><StatisticsTable data={table}/></section>)}</div>}
  </>
}

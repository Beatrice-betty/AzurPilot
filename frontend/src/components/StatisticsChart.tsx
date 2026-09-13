import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { LineChart, CandlestickChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, DataZoomComponent, ToolboxComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { StatSeries } from '../api/types'
import { Empty } from './ui'
import { StatisticsTable } from './StatisticsTable'
import { aggregatePoints } from './statisticsData'

echarts.use([LineChart, CandlestickChart, GridComponent, TooltipComponent, DataZoomComponent, ToolboxComponent, CanvasRenderer])

export function StatisticsChart({series}: {series: StatSeries[]}) {
  const [key, setKey] = useState(series.find(item => item.points.length)?.key ?? series[0]?.key ?? '')
  const [mode, setMode] = useState('line')
  const [bucket, setBucket] = useState(0)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    if (!expanded) return
    const close = (event: KeyboardEvent) => {if (event.key === 'Escape') setExpanded(false)}
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [expanded])
  const element = useRef<HTMLDivElement>(null)
  const current = series.find(item => item.key === key) ?? series[0]
  const points = useMemo(() => (current?.points ?? []).filter(point => (!from || point.time.replace(' ', 'T') >= from) && (!to || point.time.replace(' ', 'T') <= `${to}:59.999`)), [current, from, to])
  const buckets = useMemo(() => aggregatePoints(points, bucket || (mode === 'candlestick' ? 60 : 0)), [points, bucket, mode])
  const values = points.map(point => point.value)
  const minimum = values.length ? Math.min(...values) : 0
  const maximum = values.length ? Math.max(...values) : 0
  useEffect(() => {
    if (!element.current || !points.length) return
    const chart = echarts.init(element.current, undefined, {locale: 'ZH'})
    function render() {
      const colors = getComputedStyle(document.documentElement)
      const text = colors.getPropertyValue('--text').trim() || '#82929f'
      chart.setOption({
        animation: false, textStyle: {color: text, fontFamily: 'Microsoft YaHei, sans-serif'},
        grid: {left: 65, right: 30, top: 65, bottom: 85},
        tooltip: {trigger: 'axis', confine: true, renderMode: 'richText', axisPointer: {type: 'cross'}},
        toolbox: {right: 20, feature: {dataZoom: {yAxisIndex: 'none', title: {zoom: '框选放大', back: '撤销缩放'}}, restore: {title: '重置视图'}, saveAsImage: {title: '保存图表', name: current.label, pixelRatio: 2}}},
        xAxis: mode === 'candlestick' ? {type: 'category', data: buckets.map(item => item.time), axisLabel: {hideOverlap: true}} : {type: 'time', axisLabel: {hideOverlap: true}},
        yAxis: {type: 'value', scale: true, splitLine: {lineStyle: {color: colors.getPropertyValue('--border').trim()}}},
        dataZoom: [{type: 'inside', zoomOnMouseWheel: 'ctrl'}, {type: 'slider', bottom: 16, height: 26}],
        series: [{name: current.label, type: mode === 'candlestick' ? 'candlestick' : 'line',
          showSymbol: points.length < 80, symbolSize: 5, connectNulls: false,
          lineStyle: {width: 2}, itemStyle: mode === 'candlestick' ? {color: '#159b88', color0: '#de7861', borderColor: '#159b88', borderColor0: '#de7861'} : {color: '#159b88'},
          data: buckets.map(item => mode === 'candlestick' ? [item.open, item.close, item.low, item.high] : [new Date(item.time.replace(' ', 'T')).getTime(), item.close]),
        }],
      }, true)
    }
    render()
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(element.current)
    const theme = new MutationObserver(render)
    theme.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']})
    return () => {observer.disconnect(); theme.disconnect(); chart.dispose()}
  }, [points, buckets, mode, current.label])
  return <section className={`panel statistics-chart ${expanded ? 'chart-expanded' : ''}`}>
    <div className="panel-heading"><h2>趋势与细节</h2><button className="text-button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起图表' : '放大查看'}</button></div>
    <div className="statistics-controls"><label>指标<select aria-label="统计资源" value={current.key} onChange={event => setKey(event.target.value)}>{series.map(item => <option value={item.key} key={item.key}>{item.label}{item.points.length ? '' : '（暂无记录）'}</option>)}</select></label>
      <label>图表<select aria-label="图表类型" value={mode} onChange={event => setMode(event.target.value)}><option value="line">折线</option><option value="candlestick">K 线（开高低收）</option></select></label>
      <label>采样粒度<select aria-label="采样粒度" value={bucket} onChange={event => setBucket(Number(event.target.value))}><option value={0}>{mode === 'candlestick' ? '每小时' : '每次记录'}</option><option value={5}>5 分钟</option><option value={60}>每小时</option><option value={1440}>每天</option></select></label>
      <label>起始时间<div className="date-input-wrap"><input type="datetime-local" aria-label="图表起始时间" value={from} className={from ? '' : 'date-empty'} onChange={event => setFrom(event.target.value)}/>{!from && <span className="date-input-placeholder" aria-hidden="true">---- / -- / --</span>}</div></label><label>结束时间<div className="date-input-wrap"><input type="datetime-local" aria-label="图表结束时间" value={to} className={to ? '' : 'date-empty'} onChange={event => setTo(event.target.value)}/>{!to && <span className="date-input-placeholder" aria-hidden="true">---- / -- / --</span>}</div></label><button className="text-button" onClick={() => {setFrom(''); setTo('')}}>全部时间</button></div>
    {from && to && from > to && <p className="preview-error" role="alert">起始时间不能晚于结束时间。</p>}
    {points.length ? <><div className="stat-metrics">{[['最新值', values.at(-1)], ['区间变化', values.at(-1)! - values[0]], ['最高值', maximum], ['最低值', minimum], ['原始记录', points.length]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{Number(value).toLocaleString(undefined, {maximumFractionDigits: 2})}</strong></div>)}</div><div ref={element} className="chart-canvas" role="img" aria-label={`${current.label}交互趋势图`}/><p className="panel-note">拖动底部滑块或框选缩放，Ctrl + 滚轮缩放；游标查看精确值。聚合曲线取每桶末值，K 线展示开、高、低、收；摘要基于原始记录。</p><StatisticsTable data={{title: `${current.label}原始记录`, columns: ['时间', '数值', '来源'], rows: points.map(point => [point.time, point.value, point.source || '—'])}}/></> : <Empty title="这段时间没有有效记录">选择其他指标或调整时间范围，任务运行后可刷新查看。</Empty>}
  </section>
}

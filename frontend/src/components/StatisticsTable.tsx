import { ArrowDown, ArrowUp } from 'lucide-react'
import { useState } from 'react'
import type { StatTable } from '../api/types'
import { downloadCsv } from './statisticsData'
import { useApp } from '../app/context'
import { localeForLanguage } from '../i18n'

export function StatisticsTable({data}: {data: StatTable}) {
  const {ui, language} = useApp()
  const [page, setPage] = useState(0)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<{index: number; descending: boolean}>()
  const rows = data.rows.filter(row => row.some(value => String(value ?? '').toLowerCase().includes(search.toLowerCase())))
  if (sort) rows.sort((a, b) => {
    const av = a[sort.index], bv = b[sort.index]
    const order = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av ?? '').localeCompare(String(bv ?? ''), localeForLanguage(language), {numeric: true})
    return order * (sort.descending ? -1 : 1)
  })
  const pages = Math.max(1, Math.ceil(rows.length / 25))
  const current = Math.min(page, pages - 1)
  return <section className="statistics-table"><div className="panel-heading"><h3>{data.title}</h3><button className="text-button" disabled={!rows.length} onClick={() => downloadCsv(data.title, [data.columns, ...rows])}>{ui('stats.exportDetails')}</button></div>{data.note && <p className="panel-note">{data.note}</p>}<div className="table-toolbar"><input aria-label={ui('stats.searchTable', {title: data.title})} value={search} onChange={event => {setSearch(event.target.value); setPage(0)}} placeholder={ui('stats.searchPlaceholder')}/><span>{ui('stats.records', {count: rows.length})}</span></div><div className="table-scroll"><table><thead><tr>{data.columns.map((column, index) => <th key={column} aria-sort={sort?.index === index ? sort.descending ? 'descending' : 'ascending' : 'none'}><button onClick={() => setSort({index, descending: sort?.index === index ? !sort.descending : false})}>{column}{sort?.index === index ? sort.descending ? <ArrowDown size={14} aria-hidden="true"/> : <ArrowUp size={14} aria-hidden="true"/> : null}</button></th>)}</tr></thead><tbody>{rows.slice(current * 25, (current + 1) * 25).map((row, index) => <tr key={index}>{row.map((value, cell) => <td key={cell}>{value == null ? '—' : typeof value === 'number' ? value.toLocaleString(localeForLanguage(language), {maximumFractionDigits: 4}) : String(value)}</td>)}</tr>)}</tbody></table>{!rows.length && <p className="panel-note">{ui('stats.noRecords')}</p>}</div><div className="table-toolbar"><button className="text-button" disabled={!current} onClick={() => setPage(current - 1)}>{ui('common.previous')}</button><span>{current + 1} / {pages}</span><button className="text-button" disabled={current + 1 === pages} onClick={() => setPage(current + 1)}>{ui('common.next')}</button></div></section>
}

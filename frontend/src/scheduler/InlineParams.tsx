/** 卡片内编辑常用参数；复杂列表、组合端口和任务覆盖参数仍在属性面板编辑。 */
import {useApp} from '../app/context'
import type {UiKey} from '../i18n'
import type {CardDefinition, Catalog, ProgramDocument, ProgramNode} from './types'

type Props = {
  card: ProgramNode; spec: CardDefinition; catalog: Catalog; document: ProgramDocument
  connectedInputs: string[]; onChange: (node: string, patch: Record<string, unknown>) => void
}
const fields: Record<string, Array<[string, string]>> = {
  resource:[['name','读取资源'], ['field','读取数值'], ['maxAge','有效期（秒）'], ['autoRefresh','过期时自动刷新']],
  task:[['name','指定任务']], execute:[['task','执行任务'],['followOriginal','检查原计划任务切换']], last_result:[['task','任务结果']],
  wait:[['seconds','等待秒数']], wait_until:[['time','等待到'],['recheckOnConfigChange','配置变更时重新判断']],
  time_window:[['start','开始时间'],['end','结束时间']],
  compare:[['operator','比较'],['a','输入 A'],['b','输入 B']], math:[['operator','运算'],['a','输入 A'],['b','输入 B']],
  logic:[['operator','逻辑'],['a','输入 A'],['b','输入 B']],
  literal:[['valueType','数据类型'],['value','常量']],
  get_variable:[['name','变量']], set_variable:[['name','变量'],['value','赋值']],
  loop:[['count','循环次数'],['condition','继续循环']],
  filter:[['rule','筛选规则']], sort:[['field','排序字段'],['descending','降序']],
  cooldown:[['key','记录标识'],['seconds','冷却秒数']], quota:[['key','配额标识'],['limit','每日上限']],
  call:[['graph','组合卡片']],
}
const valueTypes = ['number','boolean','string','time','duration','task','tasks','resource','result','list','object','any']
const valueTypeLabels: Record<string,string> = {number:'数值', boolean:'布尔', string:'文本', time:'时间', duration:'时长', task:'任务', tasks:'任务列表', resource:'资源记录', result:'任务结果', list:'列表', object:'对象', any:'任意值'}

export function InlineParams({card,spec,catalog,document,connectedInputs,onChange}: Props) {
  const {ui,t} = useApp()
  const values = {...spec.params,...card.params}
  const resourceName = (name: string) => name.startsWith('Emotion') ? `舰队 ${name.slice(-1)} 心情` : ui(`resource.${name}` as UiKey)
  const taskName = (name: string) => {const translated=t(`Task.${name}.name`); return translated.startsWith('Task.') ? name : translated}
  const update = (key: string, value: unknown) => onChange(card.id, {...{[key]:value}, ...(key === 'valueType' ? {value:value === 'boolean' ? false : ['number','duration'].includes(String(value)) ? 0 : ['list','tasks'].includes(String(value)) ? [] : ['object','resource','task','result'].includes(String(value)) ? {} : ''} : {})})
  if (!fields[card.type]) return null
  return <div className="program-card-settings nodrag nopan nowheel nokey">
    {fields[card.type]!.map(([key,label]) => {
      const value = values[key], connected = connectedInputs.includes(key)
      let options: Array<[string,string]> | undefined
      if (key === 'name' && card.type === 'resource') options = catalog.resources.map(r => [r.name,resourceName(r.name)])
      else if ((key === 'name' && card.type === 'task') || key === 'task') options = catalog.tasks.map(task => [task.name,taskName(task.name)])
      else if (key === 'field' && card.type === 'resource') options = [['value',values.name === 'ActionPoint' ? '当前行动力' : '当前值'],['limit','上限'],['total',values.name === 'ActionPoint' ? '总行动力（含体力箱）' : '总量']]
      else if (key === 'name' && card.type.includes('variable')) options = document.variables.map(v => [v.name,v.name])
      else if (key === 'graph') options = document.subgraphs.map(s => [s.id,s.name])
      else if (key === 'valueType') options = valueTypes.map(type => [type,valueTypeLabels[type]!])
      else if (key === 'operator') options = (card.type === 'logic' ? ['and','or','not'] : card.type === 'math' ? ['+','-','*','/','%','min','max'] : ['==','!=','>','>=','<','<=']).map(op => [op,({and:'且',or:'或',not:'非'} as Record<string,string>)[op] ?? op])
      else if (key === 'rule') options = [['enabled','已启用'],['due','已到期'],['field','按字段判断']]
      if (!options && typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return null
      const inputId = `inline-${card.id}-${key}`
      return <div key={key} className={typeof value === 'boolean' && !options ? 'program-card-checkbox' : 'program-card-field'}>
        <label htmlFor={inputId}>{label}</label>
        {options ? <select id={inputId} aria-label={label} disabled={connected} value={connected ? '__connected' : String(value ?? '')} onChange={e => update(key,e.target.value)}>{connected ? <option value="__connected">由连线提供</option> : <option value="">请选择…</option>}{options.map(([option,text]) => <option key={option} value={option}>{text}</option>)}</select>
          : typeof value === 'boolean' ? <input id={inputId} type="checkbox" aria-label={label} disabled={connected} checked={Boolean(value)} onChange={e => update(key,e.target.checked)}/>
          : <input id={inputId} aria-label={label} type={typeof value === 'number' ? 'number' : key === 'start' || key === 'end' || (key === 'time' && String(value).length <= 5) ? 'time' : 'text'} disabled={connected} value={String(value)} onChange={e => update(key,typeof value === 'number' ? Number(e.target.value) : e.target.value)}/>}
        {connected && <small>由连线提供</small>}
      </div>
    })}
    {card.type === 'resource' && <small className="program-card-resource-note">{catalog.resources.find(r => r.name === values.name)?.refreshable ? '可在任务边界刷新' : '读取任务观察记录'}</small>}
  </div>
}

import { useState, type ReactNode } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { ArrowRight, Bell, ChevronRight, CircleAlert, Code2, Database, Image, Layers3, Search, Server, Settings2, Sparkles, Terminal, Trash2, X } from 'lucide-react'
import type { Value } from '../api/types'
import { useApp } from '../app/context'
import { FieldInput } from '../components/FieldInput'
import { SegmentedControl } from '../components/SegmentedControl'
import { GlassMaterial } from '../components/GlassMaterial'
import { Empty, ErrorBox, Loading, Modal, PageTitle, StatusBadge } from '../components/ui'

function DevField({id, label, help, multiline = false, children}: {id: string; label: string; help?: string; multiline?: boolean; children: ReactNode}) {
  return <div className={`field-row ${multiline ? 'field-row-multiline' : ''}`}>
    <div className="field-label"><label htmlFor={id}>{label}</label>{help && <p>{help}</p>}</div>
    <div className="field-control">{children}</div>
  </div>
}

export function DevControls() {
  const {devMode, setDevMode, notify} = useApp()
  const navigate = useNavigate()
  const [text, setText] = useState('AzurPilot')
  const [number, setNumber] = useState(25548)
  const [password, setPassword] = useState('developer')
  const [select, setSelect] = useState<Value>('Auto')
  const [dateTime, setDateTime] = useState<Value>('2026-09-15 09:00:00')
  const [month, setMonth] = useState('2026-09')
  const [toggle, setToggle] = useState<Value>(true)
  const [multi, setMulti] = useState<Value>(['Alas', 'Opsi'])
  const [textarea, setTextarea] = useState<Value>('这里用于观察多行输入框的字号、行高、圆角与聚焦状态。\n修改样式后，这个页面会直接体现真实控件效果。')
  const [yaml, setYaml] = useState<Value>('Scheduler:\n  Enable: true\n  SuccessInterval: 30')
  const [segment, setSegment] = useState<'logs' | 'preview'>('logs')
  const [modalOpen, setModalOpen] = useState(false)
  const [blur, setBlur] = useState(24)
  const [saturation, setSaturation] = useState(130)
  const [glassOpacity, setGlassOpacity] = useState(72)
  const [radius, setRadius] = useState(26)
  const [shadow, setShadow] = useState(24)
  const [demoTab, setDemoTab] = useState('资源')

  if (!devMode) return <Navigate to="/" replace/>

  function disableDevMode() {
    setDevMode(false)
    notify('开发者模式已关闭')
    navigate('/', {replace: true})
  }

  return <>
    <PageTitle title="开发者 · 控件预览" actions={<button className="button secondary" onClick={disableDevMode}><X size={15}/>退出 Dev 模式</button>}/>

    <section className="panel dev-intro">
      <div><Code2 size={20}/><div><strong>UI Playground</strong><p>集中展示项目真实控件与视觉系统。调整 tokens.css、components.css、apple.css 后可在这里一次检查各种状态。</p></div></div>
      <span className="small-label">仅 Dev 模式可见</span>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><div><Sparkles size={18}/><h2 data-text="视觉效果实验室">视觉效果实验室</h2></div><span className="small-label">实时调参</span></div>
      <div className="dev-effect-lab">
        <div className="dev-effect-stage">
          <div className="dev-effect-wallpaper" aria-hidden="true"><i/><i/><i/><span>AzurPilot</span></div>
          <div className="dev-effect-glass" style={{
            backdropFilter: `blur(${blur}px) saturate(${saturation}%)`,
            WebkitBackdropFilter: `blur(${blur}px) saturate(${saturation}%)`,
            background: `color-mix(in srgb, var(--surface) ${glassOpacity}%, transparent)`,
            borderRadius: `${radius}px`,
            boxShadow: `0 ${Math.round(shadow / 3)}px ${shadow * 2}px #00000020, inset 0 1px 0 #ffffff66`,
          }}>
            <span className="small-label">Backdrop Glass</span><strong>模糊 / 饱和 / 透明 / 圆角 / 阴影</strong><p>后面的色块和文字用于判断玻璃层对背景细节、亮度和色彩的处理。</p>
          </div>
        </div>
        <div className="dev-effect-controls">
          <label>Blur <strong>{blur}px</strong><input type="range" min="0" max="48" value={blur} onChange={event => setBlur(Number(event.target.value))}/></label>
          <label>Saturate <strong>{saturation}%</strong><input type="range" min="70" max="180" value={saturation} onChange={event => setSaturation(Number(event.target.value))}/></label>
          <label>Surface <strong>{glassOpacity}%</strong><input type="range" min="0" max="100" value={glassOpacity} onChange={event => setGlassOpacity(Number(event.target.value))}/></label>
          <label>Radius <strong>{radius}px</strong><input type="range" min="0" max="48" value={radius} onChange={event => setRadius(Number(event.target.value))}/></label>
          <label>Shadow <strong>{shadow}px</strong><input type="range" min="0" max="48" value={shadow} onChange={event => setShadow(Number(event.target.value))}/></label>
        </div>
      </div>
      <div className="dev-blur-presets">
        {[0, 6, 12, 18, 24, 32].map(value => <div key={value} className="dev-blur-preset-wrap"><div className="dev-blur-preset-bg"><div style={{backdropFilter: `blur(${value}px)`, WebkitBackdropFilter: `blur(${value}px)`}}>Blur {value}px</div></div><span>{value === 0 ? '无模糊' : value <= 12 ? '轻度' : value <= 24 ? '中度' : '重度'}</span></div>)}
      </div>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><div><Layers3 size={18}/><h2 data-text="玻璃、表面与层级">玻璃、表面与层级</h2></div></div>
      <div className="dev-surface-grid">
        <div className="dev-surface-sample dev-surface-plain"><span>Surface</span><strong>普通表面</strong><small>var(--surface)</small></div>
        <div className="dev-surface-sample dev-surface-muted"><span>Muted</span><strong>弱化表面</strong><small>var(--surface-muted)</small></div>
        <div className="dev-surface-sample dev-surface-accent"><span>Accent</span><strong>强调表面</strong><small>var(--accent-soft)</small></div>
        <div className="dev-surface-sample dev-surface-glass"><GlassMaterial/><span>Glass</span><strong>项目真实玻璃材质</strong><small>GlassMaterial</small></div>
      </div>
      <div className="dev-shadow-grid">
        {[['无阴影', 'none'], ['轻阴影', '0 4px 14px #00000010'], ['面板阴影', 'var(--glass-shadow)'], ['浮层阴影', '0 18px 56px #0003'], ['Modal 阴影', '0 24px 100px #0003']].map(([label, value]) => <div key={label} className="dev-shadow-sample" style={{boxShadow: value}}><strong>{label}</strong><code>{value}</code></div>)}
      </div>
      <div className="dev-radius-grid">
        {[0, 6, 10, 14, 18, 22, 26, 32, 999].map(value => <div key={value}><span style={{borderRadius: `${value}px`}}/><small>{value === 999 ? 'Pill' : `${value}px`}</small></div>)}
      </div>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><h2 data-text="颜色与设计 Token">颜色与设计 Token</h2></div>
      <div className="dev-token-grid">
        {[
          ['Text', '--text'], ['Muted', '--muted'], ['Accent', '--accent'], ['Accent Soft', '--accent-soft'],
          ['Surface', '--surface'], ['Surface Muted', '--surface-muted'], ['Border', '--border'], ['Glass Tint', '--glass-tint'],
          ['Glass Edge', '--glass-edge'], ['Green', '--green'], ['Red', '--red'], ['Background', '--bg'],
        ].map(([label, token]) => <div className="dev-token" key={token}><span style={{background: `var(${token})`}}/><div><strong>{label}</strong><code>var({token})</code></div></div>)}
      </div>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><h2 data-text="文字层级与内容样式">文字层级与内容样式</h2></div>
      <div className="dev-type-grid">
        <div><span className="small-label">Page Title</span><h1>AzurPilot Developer</h1></div>
        <div><span className="small-label">Section Heading</span><h2>界面样式检查</h2></div>
        <div><span className="small-label">Card Heading</span><h3>实例运行状态</h3></div>
        <div><span className="small-label">Body</span><p>用于检查正文文字的颜色、字重、行高和中英文混排效果。The quick brown fox jumps over the lazy dog.</p></div>
        <div><span className="small-label">Muted / Secondary</span><p className="muted">这是弱化说明文本，用来观察背景变化时的可读性。</p></div>
        <div><span className="small-label">Code</span><code>Scheduler.Enable = true</code></div>
        <div><span className="small-label">数字</span><strong className="dev-numeric">12,345.67 / 99.8%</strong></div>
        <div><span className="small-label">省略</span><div className="dev-ellipsis">这是一段故意非常非常非常非常非常长的文本，用于观察单行溢出、省略号和窄容器表现</div></div>
      </div>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><div><Code2 size={18}/><h2 data-text="按钮与操作">按钮与操作</h2></div></div>
      <div className="dev-control-block">
        <div className="dev-control-label"><strong>按钮样式</strong><span>默认、主按钮、次按钮、危险、禁用</span></div>
        <div className="dev-button-row">
          <button className="button">默认按钮</button>
          <button className="button primary">主按钮</button>
          <button className="button secondary">次按钮</button>
          <button className="button danger"><Trash2 size={15}/>危险操作</button>
          <button className="button danger subtle">危险 · Subtle</button>
          <button className="button primary" disabled>禁用按钮</button>
        </div>
      </div>
      <div className="dev-control-block">
        <div className="dev-control-label"><strong>轻量操作</strong><span>图标按钮、文字按钮、弹窗</span></div>
        <div className="dev-button-row">
          <button className="icon-button" aria-label="设置"><Settings2 size={18}/></button>
          <button className="icon-button" aria-label="关闭"><X size={18}/></button>
          <button className="text-button">文字按钮</button>
          <button className="button secondary" onClick={() => setModalOpen(true)}>打开 Modal</button>
        </div>
      </div>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><h2 data-text="表单控件">表单控件</h2></div>
      <DevField id="dev-text" label="文本输入框" help="普通 text input，包括 placeholder、focus 与输入文字。">
        <FieldInput id="dev-text" label="文本输入框" value={text} onChange={value => setText(String(value ?? ''))}/>
      </DevField>
      <DevField id="dev-number" label="数字输入框" help="项目 FieldInput 的 number/int 样式。">
        <FieldInput id="dev-number" label="数字输入框" type="number" value={number} onChange={value => setNumber(Number(value))}/>
      </DevField>
      <DevField id="dev-password" label="密码输入框">
        <FieldInput id="dev-password" label="密码输入框" type="password" value={password} onChange={value => setPassword(String(value ?? ''))}/>
      </DevField>
      <DevField id="dev-select" label="下拉框" help="使用项目真实 select 渲染路径。">
        <FieldInput id="dev-select" label="下拉框" value={select} options={['Auto', 'ADB', 'Nemulator']} onChange={setSelect}/>
      </DevField>
      <DevField id="dev-datetime" label="日期时间">
        <FieldInput id="dev-datetime" label="日期时间" type="datetime" value={dateTime} onChange={setDateTime}/>
      </DevField>
      <DevField id="dev-month" label="月份选择">
        <input id="dev-month" type="month" value={month} onChange={event => setMonth(event.target.value)}/>
      </DevField>
      <DevField id="dev-toggle" label="开关" help="开启、关闭与禁用状态。">
        <div className="dev-inline-controls">
          <FieldInput id="dev-toggle" label="开关" type="bool" value={toggle} onChange={setToggle}/>
          <FieldInput id="dev-toggle-off" label="关闭状态" type="bool" value={false} onChange={() => undefined}/>
          <FieldInput id="dev-toggle-disabled" label="禁用开关" type="bool" value={true} disabled onChange={() => undefined}/>
        </div>
      </DevField>
      <DevField id="dev-multi" label="多选框" help="项目 multiselect / checkbox 组合。">
        <FieldInput id="dev-multi" label="多选框" type="multiselect" value={multi} options={['Alas', 'Opsi', 'Commission', 'Event']} onChange={setMulti}/>
      </DevField>
      <DevField id="dev-disabled" label="禁用输入框">
        <FieldInput id="dev-disabled" label="禁用输入框" value="不可编辑" disabled onChange={() => undefined}/>
      </DevField>
      <DevField id="dev-invalid" label="错误输入框" help="用于调整 aria-invalid 对应的错误状态。">
        <FieldInput id="dev-invalid" label="错误输入框" value="invalid-value" invalid onChange={() => undefined}/>
      </DevField>
      <DevField id="dev-search" label="带图标输入框">
        <div className="input-icon"><Search size={15}/><input id="dev-search" placeholder="搜索任务、配置或实例…"/></div>
      </DevField>
      <DevField id="dev-textarea" label="多行输入框" multiline>
        <FieldInput id="dev-textarea" label="多行输入框" type="textarea" value={textarea} onChange={setTextarea}/>
      </DevField>
      <DevField id="dev-yaml" label="YAML 编辑器" help="CodeMirror 实际编辑器，用于检查代码字体、边框、选中与暗色主题。" multiline>
        <FieldInput id="dev-yaml" label="YAML 编辑器" type="yaml" value={yaml} onChange={setYaml}/>
      </DevField>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><h2 data-text="选择器与状态">选择器与状态</h2></div>
      <div className="dev-control-block">
        <div className="dev-control-label"><strong>分段控制器</strong><span>复用运行监控页的 segmented control。</span></div>
        <SegmentedControl label="开发者分段控制器" value={segment} onChange={setSegment} options={[
          {value: 'logs', label: <><Terminal size={15}/>日志</>},
          {value: 'preview', label: <><Image size={15}/>截图</>},
        ]}/>
      </div>
      <div className="dev-control-block">
        <div className="dev-control-label"><strong>状态徽标</strong><span>运行、待命、错误、更新。</span></div>
        <div className="dev-button-row">
          <StatusBadge status="running"/><StatusBadge status="stopped"/><StatusBadge status="error"/><StatusBadge status="updating"/>
          <span className="count-badge">12</span><span className="live-label"><i/>LIVE</span><span className="update-notice"><Bell size={13}/>新版本可用</span>
        </div>
      </div>
      <div className="dev-control-block">
        <div className="dev-control-label"><strong>统计页 Tabs</strong><span>检查胶囊滑块、长文本和选中态。</span></div>
        <SegmentedControl label="开发者统计分类" value={demoTab} onChange={setDemoTab} options={['资源','掉落','行动','委托'].map(value => ({value, label: value}))}/>
      </div>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><h2 data-text="导航、卡片与层级关系">导航、卡片与层级关系</h2></div>
      <div className="dev-layout-grid">
        <div className="dev-nav-preview">
          <span className="small-label">Primary Navigation</span>
          <nav className="primary-nav">
            <a href="#dev-nav" onClick={event => event.preventDefault()}><Server size={18}/>普通导航</a>
            <a href="#dev-nav" className="active" onClick={event => event.preventDefault()}><Database size={18}/>当前页面<span className="nav-pill">DEV</span></a>
            <a href="#dev-nav" onClick={event => event.preventDefault()}><Settings2 size={18}/>悬停查看</a>
          </nav>
          <div className="task-group-button expanded"><Layers3 size={18} className="task-group-icon"/><span className="task-group-title">一级任务菜单</span><ChevronRight size={13} className="task-group-arrow"/></div>
          <div className="task-submenu-list dev-submenu-list">
            <a className="task-submenu-item active" href="#dev-sub" onClick={event => event.preventDefault()}><span className="task-submenu-dot"/><span className="task-submenu-item-text">当前子菜单</span></a>
            <a className="task-submenu-item" href="#dev-sub" onClick={event => event.preventDefault()}><span className="task-submenu-dot"/><span className="task-submenu-item-text">普通子菜单</span></a>
          </div>
        </div>
        <div className="dev-card-preview">
          <span className="small-label">Instance Card</span>
          <div className="instance-card panel">
            <div className="instance-card-heading"><span className="home-instance-icon"><Server size={22}/></span><StatusBadge status="running"/></div>
            <h3>dev-instance</h3><div className="instance-device"><span>ADB</span><span>127.0.0.1:5555</span></div>
            <div className="instance-card-footer"><span>正在运行主线任务</span><ArrowRight size={17}/></div>
          </div>
        </div>
        <div className="dev-metric-preview">
          <span className="small-label">Metric Cards</span>
          <div className="summary-metrics stat-metrics">
            <section><span>金币</span><strong>52,840<small>/ 600,000</small></strong></section>
            <section><span>石油</span><strong>14,320<small>/ 25,000</small></strong></section>
            <section><span>运行次数</span><strong>128<small>次</small></strong></section>
          </div>
        </div>
      </div>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><h2 data-text="数据、表格与滚动区域">数据、表格与滚动区域</h2></div>
      <div className="statistics-table dev-table-preview">
        <div className="table-toolbar"><div className="input-icon"><Search size={14}/><input placeholder="搜索记录…"/></div><span>3 条记录</span></div>
        <div className="table-scroll">
          <table><thead><tr><th>时间</th><th>任务</th><th>状态</th><th>耗时</th></tr></thead><tbody>
            <tr><td>09:18:02</td><td>主线出击</td><td><span className="status running"><i/>完成</span></td><td>01:42</td></tr>
            <tr><td>09:15:43</td><td>科研项目</td><td><span className="status updating"><i/>同步</span></td><td>00:18</td></tr>
            <tr><td>09:12:10</td><td>委托检查</td><td><span className="status stopped"><i/>待命</span></td><td>00:06</td></tr>
          </tbody></table>
        </div>
      </div>
      <div className="dev-scroll-sample"><div>{Array.from({length: 12}, (_, index) => <p key={index}><code>{String(index + 1).padStart(2, '0')}</code> 这是用于检查滚动条、行高和长容器边缘裁切的内容。</p>)}</div></div>
    </section>

    <section className="panel config-group">
      <div className="panel-heading"><h2 data-text="反馈状态">反馈状态</h2></div>
      <div className="dev-feedback-grid">
        <div><span className="small-label">错误提示</span><ErrorBox message="这是用于检查错误提示布局的示例消息。" retry={() => notify('已触发重试示例')}/></div>
        <div><span className="small-label">加载状态</span><div className="dev-state-box"><Loading/></div></div>
        <div><span className="small-label">空状态</span><div className="dev-state-box"><Empty icon={<CircleAlert size={34}/>} title="暂无内容">用于观察空状态的字号、间距和图标。</Empty></div></div>
      </div>
    </section>

    {modalOpen && <Modal title="Modal 样式预览" onClose={() => setModalOpen(false)}><div className="form-stack"><p className="muted">这里使用项目真实 Modal、表单与按钮样式。</p><label>示例输入<input defaultValue="AzurPilot Dev Mode"/></label><div className="dev-button-row"><button className="button secondary" onClick={() => setModalOpen(false)}>取消</button><button className="button primary" onClick={() => setModalOpen(false)}>确认</button></div></div></Modal>}
  </>
}

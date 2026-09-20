import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, GripVertical, RotateCcw } from 'lucide-react'
import { AutoTextarea } from './AutoTextarea'
import { useApp } from '../app/context'
import { formatTaskPriority, mergeTaskPriority, reorderTaskPriority, schedulerTasks } from '../app/taskPriority'

interface Props {
  id: string; value: string; fallback: string; onChange: (value: string) => void
  disabled?: boolean; label: string; invalid?: boolean
}

interface DragState {
  index: number; pointerId: number; grabOffset: number; clientY: number; autoScroll: number
  timer?: ReturnType<typeof setInterval>
  move: (event: PointerEvent) => void
  end: (commit: boolean) => void
}

/** 拖到第几行之前：与各行的垂直中点比较，被拖的行本身不参与比较。 */
export function insertionIndexAt(rows: {top: number; height: number}[], clientY: number, skip: number): number {
  for (let index = 0; index < rows.length; index++) {
    if (index === skip) continue
    if (clientY < rows[index].top + rows[index].height / 2) return index
  }
  return rows.length
}

/**
 * 任务调度优先级的拖动编辑器。
 *
 * 列表里是合并后的实际生效顺序（与运行器的 merge_task_priority 同一套规则）：
 * 配置里还没记录的新任务会按内置默认优先级插到合适位置，拖动保存的即是这份顺序。
 * 拖动用指针事件实现，鼠标与触摸一致；行列末尾的上下按钮补上键盘操作。
 * 文本模式保留原输入框，方便直接粘贴他人分享的优先级字符串。
 */
export function TaskPriorityField({id, value, fallback, onChange, disabled, label, invalid}: Props) {
  const {t, ui, schema} = useApp()
  const [textMode, setTextMode] = useState(false)
  const [drag, setDrag] = useState<{index: number; offset: number} | undefined>(undefined)
  const [landing, setLanding] = useState<number | undefined>(undefined)
  const listRef = useRef<HTMLOListElement>(null)
  const dragRef = useRef<DragState | undefined>(undefined)
  const landingRef = useRef<number | undefined>(undefined)
  const frameRef = useRef(0)
  const available = useMemo(() => schedulerTasks(schema?.args), [schema])
  const order = useMemo(() => mergeTaskPriority(value, fallback, available), [value, fallback, available])

  function commit(tasks: string[]) {
    onChange(formatTaskPriority(tasks))
  }

  function rows() {
    return Array.from(listRef.current?.querySelectorAll<HTMLElement>('.task-priority-row') ?? [])
  }

  function setLandingIndex(next: number | undefined) {
    landingRef.current = next
    setLanding(next)
  }

  /** 指针贴近列表上下边缘时持续滚动，长列表也能把任务拖到看不见的位置。 */
  function updateAutoScroll(state: DragState) {
    const list = listRef.current
    if (!list) return
    const box = list.getBoundingClientRect()
    const threshold = Math.min(120, Math.max(48, box.height * .18))
    const fromTop = state.clientY - box.top
    const fromBottom = box.bottom - state.clientY
    let speed = 0
    if (fromTop < threshold) speed = -Math.ceil((threshold - fromTop) / threshold * 24)
    else if (fromBottom < threshold) speed = Math.ceil((threshold - fromBottom) / threshold * 24)
    state.autoScroll = speed
    if (speed && state.timer === undefined) {
      state.timer = setInterval(() => {
        const current = dragRef.current
        if (!current?.autoScroll) return
        list.scrollTop += current.autoScroll
        paint(current)
      }, 16)
    } else if (!speed && state.timer !== undefined) {
      clearInterval(state.timer)
      state.timer = undefined
    }
  }

  /** 按最新的滚动位置重算浮起偏移与落点，一帧只跑一次。 */
  function paint(state: DragState) {
    const items = rows()
    const row = items[state.index]
    if (!row) return
    setDrag({index: state.index, offset: state.clientY - state.grabOffset - row.getBoundingClientRect().top})
    setLandingIndex(insertionIndexAt(items.map(item => item.getBoundingClientRect()), state.clientY, state.index))
    updateAutoScroll(state)
  }

  function schedule(state: DragState) {
    cancelAnimationFrame(frameRef.current)
    frameRef.current = requestAnimationFrame(() => {
      if (dragRef.current === state) paint(state)
    })
  }

  function startDrag(event: React.PointerEvent<HTMLLIElement>, index: number) {
    if (disabled || dragRef.current || event.button !== 0) return
    // 行内的上下移动按钮自己处理点击，不从它们起拖。
    if ((event.target as HTMLElement).closest('button')) return
    const box = event.currentTarget.getBoundingClientRect()
    event.preventDefault()
    const state: DragState = {
      index, pointerId: event.pointerId, grabOffset: event.clientY - box.top, clientY: event.clientY, autoScroll: 0,
      move: () => {},
      end: () => {},
    }
    state.move = moveEvent => {
      if (moveEvent.pointerId !== state.pointerId) return
      moveEvent.preventDefault()
      state.clientY = moveEvent.clientY
      schedule(state)
    }
    state.end = (commitDrag: boolean) => {
      const landingIndex = landingRef.current
      dragRef.current = undefined
      if (state.timer !== undefined) clearInterval(state.timer)
      cancelAnimationFrame(frameRef.current)
      window.removeEventListener('pointermove', state.move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel)
      document.body.classList.remove('task-priority-dragging')
      setDrag(undefined)
      setLandingIndex(undefined)
      // 原地松手（点一下行没拖动）不写配置，免得白存一次同样的顺序。
      const moved = landingIndex !== undefined && landingIndex !== state.index && landingIndex !== state.index + 1
      if (commitDrag && moved) commit(reorderTaskPriority(order, state.index, landingIndex))
    }
    const up = (upEvent: PointerEvent) => {
      if (upEvent.pointerId === state.pointerId) state.end(true)
    }
    const cancel = (cancelEvent: PointerEvent) => {
      if (cancelEvent.pointerId === state.pointerId) state.end(false)
    }
    dragRef.current = state
    setDrag({index, offset: 0})
    setLandingIndex(index)
    document.body.classList.add('task-priority-dragging')
    window.addEventListener('pointermove', state.move, {passive: false})
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel)
  }

  // 组件在拖动途中卸载（切换参数页）时收尾，别把监听与定时器留在 window 上。
  const endDragRef = useRef(() => {})
  endDragRef.current = () => dragRef.current?.end(false)
  useEffect(() => () => endDragRef.current(), [])

  function move(from: number, to: number) {
    if (from !== to) commit(reorderTaskPriority(order, from, to))
  }

  return <div className={`task-priority${disabled ? ' is-disabled' : ''}`}>
    <div className="task-priority-bar">
      <button type="button" className="text-button" onClick={() => setTextMode(mode => !mode)}>
        {textMode ? ui('task.priorityShowList') : ui('task.priorityEditText')}
      </button>
      <button type="button" className="text-button" disabled={disabled} onClick={() => commit(mergeTaskPriority(fallback, fallback, available))}>
        <RotateCcw size={13}/>{ui('task.priorityReset')}
      </button>
    </div>
    {textMode
      ? <AutoTextarea id={id} value={value} label={label} invalid={invalid} disabled={disabled} onChange={onChange}/>
      : <ol className="task-priority-list" id={id} ref={listRef} aria-label={label}>
        {order.map((task, index) => {
          const name = t(`Task.${task}.name`)
          const moveUp = ui('task.priorityMoveUp', {task: name})
          const moveDown = ui('task.priorityMoveDown', {task: name})
          // 落点正好是被拖行的原位时不画插入线：拖了等于没拖。
          const dragIndex = drag?.index
          const dragOffset = drag?.offset ?? 0
          const landed = dragIndex !== undefined && landing !== undefined && landing !== dragIndex && landing !== dragIndex + 1
          const classes = ['task-priority-row']
          if (dragIndex === index) classes.push('dragging')
          if (landed && landing === index) classes.push('drop-before')
          if (landed && landing === order.length && index === order.length - 1) classes.push('drop-after')
          return <li
            key={task}
            data-task={task}
            className={classes.join(' ')}
            style={dragIndex === index ? {transform: `translateY(${dragOffset}px)`} : undefined}
            onPointerDown={event => startDrag(event, index)}>
            <span className="task-priority-index">{index + 1}</span>
            <span className="task-priority-grip" aria-hidden="true"><GripVertical size={15}/></span>
            <span className="task-priority-name">{name}</span>
            <span className="task-priority-code" title={task}>{task}</span>
            {!disabled && <span className="task-priority-move">
              <button type="button" className="icon-button" disabled={index === 0} aria-label={moveUp} title={moveUp} onClick={() => move(index, index - 1)}><ArrowUp size={14}/></button>
              <button type="button" className="icon-button" disabled={index === order.length - 1} aria-label={moveDown} title={moveDown} onClick={() => move(index, index + 1)}><ArrowDown size={14}/></button>
            </span>}
          </li>
        })}
      </ol>}
  </div>
}

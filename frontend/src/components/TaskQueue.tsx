import { useLayoutEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, CirclePlay, Hourglass, ListTodo } from 'lucide-react'
import type { Overview } from '../api/types'
import { useApp } from '../app/context'
import type { UiKey } from '../i18n'

const taskStateLabel = {
  running: 'scheduler.running',
  pending: 'scheduler.pending',
  waiting: 'scheduler.waiting',
} as const

const taskGroups = [
  {state: 'running', label: 'scheduler.running', empty: 'scheduler.noRunning', icon: CirclePlay},
  {state: 'pending', label: 'scheduler.pending', empty: 'scheduler.noPending', icon: ListTodo},
  {state: 'waiting', label: 'scheduler.waiting', empty: 'scheduler.noWaiting', icon: Hourglass},
] as const

/** 条目位置。用布局坐标：它不含脚本正在写的 transform，
 *  动画没停时也能读到真实位置。 */
export type Box = {left: number; top: number}

/** 一次渲染前后同一条目发生位移时的起止偏移；不足 1px 的抖动不算移动。
 *  返回空表示不必为这条播动画。 */
export function movedBy(before: Box | undefined, after: Box) {
  if (!before) return null
  const dx = before.left - after.left
  const dy = before.top - after.top
  if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return null
  return {dx, dy}
}

/**
 * 任务计划：正在运行 / 待运行 / 等待中三组。
 *
 * 条目显示任务名、执行时间与状态徽章。时间只写值不带「执行时间：」前缀，
 * 运行中的任务同样把时间列出来（只标状态的话就看不出它排在什么时候）。
 * `onNavigate` 供移动端抽屉在点击任务后收起使用，桌面端不传。
 *
 * 三组同处一个容器，条目在组间移动时能被测到位置变化并连续平移过去。
 * 被新条目推下去的那些同样在这个容器里，所以推挤也是连续的。
 * 新加入的条目淡入，其余条目按测到的位移平移。
 */
export function TaskQueue({instance, data, onNavigate}: {instance: string; data?: Overview; onNavigate?: () => void}) {
  const {t, ui} = useApp()
  const positions = useRef(new Map<string, Box>())
  const known = useRef(new Set<string>())
  const list = useRef<HTMLDivElement>(null)
  const arrived = useRef(new Set<string>())

  useLayoutEffect(() => {
    const container = list.current
    if (!container) return
    const previous = positions.current
    const next = new Map<string, Box>()
    const moving: {element: HTMLElement; dx: number; dy: number}[] = []
    for (const body of container.querySelectorAll<HTMLElement>('.rail-queue-body')) {
      const state = body.closest('.rail-queue-group')!.className.split(' ').pop()!
      for (const element of body.querySelectorAll<HTMLElement>('[data-task]')) {
        // 键带上组名：同一条目换组后是另一个节点，位置按组分别记账。
        const key = `${state}/${element.dataset.task}`
        const after = {left: element.offsetLeft, top: element.offsetTop}
        next.set(key, after)
        const shift = movedBy(previous.get(key), after)
        if (shift) moving.push({element, ...shift})
      }
    }
    // 这一轮记下的键并进档案，跨渲染累计。
    arrived.current = new Set([...next.keys()].filter(key => !known.current.has(key)))
    for (const key of next.keys()) known.current.add(key)
    positions.current = next
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return
    for (const {element, dx, dy} of moving) {
      element.animate(
        [{transform: `translate(${dx}px, ${dy}px)`}, {transform: 'none'}],
        {duration: 320, easing: 'cubic-bezier(.22, .61, .36, 1)'},
      )
    }
  })

  // 同一轮进来的多条依次错开，避免叠在一起看不出是几条。
  let arrival = -1

  return <div className="rail-task-list" ref={list}>
    {data?.tasks.length ? taskGroups.map(group => {
      const tasks = data.tasks.filter(task => task.state === group.state)
      const GroupIcon = group.icon
      return <section className={`rail-queue-group ${group.state}`} key={group.state} aria-label={ui(group.label as UiKey)}>
        <div className="rail-queue-heading">
          <div><GroupIcon size={16}/><strong>{ui(group.label as UiKey)}</strong></div>
          <span>{tasks.length}</span>
        </div>
        <div className="rail-queue-body">
          {tasks.length ? tasks.map(task => {
            const nextRun = task.nextRun?.replace('T', ' ').trim()
            // backwards 让起始帧只覆盖延迟期，动画本身不带 fill。
            const style = arrived.current.has(`${group.state}/${task.name}`)
              ? {animation: `rail-task-in .26s cubic-bezier(.22, .61, .36, 1) ${arrival++ * 45}ms backwards`}
              : undefined
            return <Link key={task.name} data-task={task.name} className="rail-task-item" style={style}
                         to={`/i/${instance}/task/${task.name}`} onClick={onNavigate}>
              <div>
                <strong>{t(`Task.${task.name}.name`)}</strong>
                {nextRun && <small>{nextRun}</small>}
              </div>
              <span className={`task-state ${task.state}`}><GroupIcon size={12}/>{ui(taskStateLabel[task.state])}</span>
              <ChevronRight size={13}/>
            </Link>
          }) : <div className="rail-queue-empty">{ui(group.empty as UiKey)}</div>}
        </div>
      </section>
    }) : <div className="rail-empty">{ui('scheduler.noEnabled')}</div>}
  </div>
}

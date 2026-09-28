import {describe, expect, it} from 'vitest'
import {compatible, duplicate, horizontalLayout} from './graph'
import type {Graph} from './types'

const graph: Graph = {
  entry:'entry', nodes:[
    {id:'entry', type:'entry', label:'', params:{}, position:{x:90,y:70}},
    {id:'loop', type:'loop', label:'', params:{count:3}, position:{x:0,y:0}},
    {id:'resource', type:'resource', label:'', params:{name:'Oil'}, position:{x:0,y:0}},
    {id:'branch', type:'branch', label:'', params:{}, position:{x:0,y:0}},
    {id:'repeat', type:'loop_end', label:'', params:{loop:'loop'}, position:{x:0,y:0}},
  ], edges:[
    {id:'1', source:'entry', sourcePort:'next', target:'loop', targetPort:'in', kind:'control'},
    {id:'2', source:'loop', sourcePort:'body', target:'branch', targetPort:'in', kind:'control'},
    {id:'3', source:'resource', sourcePort:'value', target:'branch', targetPort:'condition', kind:'data'},
    {id:'4', source:'branch', sourcePort:'yes', target:'repeat', targetPort:'in', kind:'control'},
  ],
}

describe('调度图编辑', () => {
  it('按执行和数据依赖横向排列，保留程序语义与原文档', () => {
    const before = structuredClone(graph), result = horizontalLayout(graph)
    const positions = new Map(result.nodes.map(n => [n.id,n.position]))
    for (const edge of graph.edges) expect(positions.get(edge.target)!.x).toBeGreaterThan(positions.get(edge.source)!.x)
    expect(positions.get('entry')!.y).not.toBe(positions.get('resource')!.y)
    expect(result.edges).toBe(graph.edges)
    expect(graph).toEqual(before)
    expect(horizontalLayout(result)).toEqual(result)
  })
  it('复制循环时重绑定结束卡片，并只复制选中节点之间的连接', () => {
    const result = duplicate(graph, new Set(['loop','branch','repeat']))
    const loop = result.nodes.find(n => n.type === 'loop')!
    expect(result.nodes.find(n => n.type === 'loop_end')!.params.loop).toBe(loop.id)
    expect(result.edges).toHaveLength(2)
    expect(result.edges.every(e => result.nodes.some(n => n.id === e.source) && result.nodes.some(n => n.id === e.target))).toBe(true)
    expect(graph.nodes.find(n => n.id === 'repeat')!.params.loop).toBe('loop')
  })
  it('只允许兼容的端口类型连接', () => {
    expect(compatible('task','tasks')).toBe(false)
    expect(compatible('number','boolean')).toBe(false)
    expect(compatible('resource','number')).toBe(false)
    expect(compatible('duration','number')).toBe(true)
    expect(compatible('tasks','list')).toBe(true)
    expect(compatible('object','resource')).toBe(true)
  })
})

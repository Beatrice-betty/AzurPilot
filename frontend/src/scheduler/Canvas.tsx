/** 拖拽的瞬时状态由画布管理，松开后才提交文档，避免测量、选择和持久图互相覆盖。 */
import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {applyEdgeChanges, applyNodeChanges, Background, Controls, MiniMap, ReactFlow, SelectionMode, useReactFlow, type Edge, type EdgeChange, type Node, type NodeChange, type ReactFlowProps} from '@xyflow/react'
import {categoryColor} from './appearance'
import type {CardDefinition, Catalog, PortType, ProgramDocument, ProgramNode} from './types'

export type CanvasNode = Node<{card: ProgramNode; spec: CardDefinition; current: boolean; invalid: boolean; catalog: Catalog; document: ProgramDocument; connectedInputs: string[]; inputTypes: Record<string,PortType>; outputTypes: Record<string,PortType>; onParamsChange: (node: string, patch: Record<string, unknown>) => void}, 'card'>
type Props = ReactFlowProps<CanvasNode> & {onPositionsCommit: (nodes: CanvasNode[]) => void}

export function ProgramCanvas({nodes: documentNodes = [], edges: documentEdges = [], onPositionsCommit, ...props}: Props) {
  const [nodes, setNodes] = useState(documentNodes)
  const [edges, setEdges] = useState(documentEdges)
  const root = useRef<HTMLDivElement>(null)
  const flow = useReactFlow<CanvasNode>()
  useEffect(() => {
    if (!root.current) return
    let previous = '', frame = 0
    const observer = new ResizeObserver(([entry]) => {
      if (!entry || !entry.contentRect.width || !entry.contentRect.height) return
      const size = `${entry.contentRect.width}:${entry.contentRect.height}`
      if (previous && size !== previous) {
        cancelAnimationFrame(frame)
        frame = requestAnimationFrame(() => void flow.fitView({padding:0.12, minZoom:0.2, maxZoom:1}))
      }
      previous = size
    })
    observer.observe(root.current)
    return () => {observer.disconnect(); cancelAnimationFrame(frame)}
  }, [flow])
  // 保留 React Flow 测得的尺寸，文档或节点装饰变化不会重新挂载节点。
  useEffect(() => {
    setNodes(previous => {
      const indexed = new Map(previous.map(node => [node.id, node]))
      return documentNodes.map(node => {
        const current = indexed.get(node.id)
        return {...current, ...node, position: current?.dragging ? current.position : node.position}
      })
    })
  }, [documentNodes])
  useEffect(() => {
    setEdges(previous => {
      const indexed = new Map(previous.map(edge => [edge.id,edge]))
      return documentEdges.map(edge => ({...indexed.get(edge.id),...edge}))
    })
  }, [documentEdges])
  const onNodesChange = useCallback((changes: NodeChange<CanvasNode>[]) => {
    setNodes(previous => applyNodeChanges(changes, previous))
  }, [])
  const onEdgesChange = useCallback((changes: EdgeChange<Edge>[]) => setEdges(previous => applyEdgeChanges(changes,previous)), [])
  const minimapColor = useCallback((node: CanvasNode) => categoryColor(node.data.spec.category), [])
  const fitViewOptions = useMemo(() => ({padding: 0.12, minZoom: 0.2, maxZoom: 1}), [])
  return <div className="program-flow" ref={root}><ReactFlow<CanvasNode> {...props} nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
    fitView fitViewOptions={fitViewOptions} minZoom={0.2} maxZoom={1.6} autoPanOnNodeDrag={false} nodeDragThreshold={0}
    panOnDrag={[1,2]} panActivationKeyCode="Space" selectionOnDrag selectionMode={SelectionMode.Partial}
    multiSelectionKeyCode={['Shift','Control','Meta']} onNodeDragStop={(_, __, moved) => onPositionsCommit(moved)}
    onSelectionDragStop={(_, moved) => onPositionsCommit(moved)}>
    <Background gap={22} size={1}/><Controls showInteractive={false}/><MiniMap pannable zoomable nodeColor={minimapColor}/>
  </ReactFlow></div>
}

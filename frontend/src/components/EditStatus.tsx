import type { Edit } from '../config/EditQueue'
import { Check, CircleAlert, CloudOff, LoaderCircle } from 'lucide-react'

export function EditStatus({edit, id, retry}: {edit?: Edit; id: string; retry: () => void}) {
  if (!edit) return null
  const Icon = edit.status === 'error' ? CircleAlert : edit.status === 'saved' ? Check : edit.status === 'saving' ? LoaderCircle : CloudOff
  return <div id={`${id}-status`} className={`edit-status ${edit.status === 'error' ? 'edit-error' : ''}`} role={edit.status === 'error' ? 'alert' : 'status'}>
    <Icon size={14} aria-hidden="true" className={edit.status === 'saving' ? 'spin' : undefined}/>
    {edit.status === 'error' ? `${edit.error} 输入已保留。` : edit.status === 'saved' ? '已保存' : edit.status === 'saving' ? '正在保存…' : '等待连接后保存…'}
    {edit.retryable && edit.status === 'error' && <button className="button subtle" onClick={retry}>重试保存</button>}
  </div>
}

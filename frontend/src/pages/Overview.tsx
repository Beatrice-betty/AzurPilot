import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Overview as OverviewData } from '../api/types'
import { useConnection } from '../app/context'
import { ErrorBox, Loading, PageTitle } from '../components/ui'
import { MonitorPanel } from '../components/MonitorPanel'
import { ResourceCards } from '../components/ResourceCards'
import { InstanceActions } from '../components/InstanceActions'

export function Overview() {
  const {instance = ''} = useParams()
  const [data, setData] = useState<OverviewData>()
  const [error, setError] = useState('')
  const connection = useConnection()

  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    void api.request('overview.get', {instance})
      .then(value => { if (active) {setData(value); setError('')} })
      .catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [instance, connection])

  useEffect(() => api.onEvent(event => {
    if (event.topic === 'overview' && (event.data as OverviewData).instance === instance) setData(event.data as OverviewData)
  }), [instance])

  if (error) return <ErrorBox message={error}/>
  if (!data) return <Loading/>

  return <>
    <PageTitle title={instance} actions={<InstanceActions instance={instance} status={data.status}/>}/>
    <ResourceCards instance={instance} resources={data.resources}/>
    <div className="overview-main">
      <MonitorPanel instance={instance}/>
    </div>
  </>
}

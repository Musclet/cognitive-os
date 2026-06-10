import React, { useEffect, useState } from 'react'
import { Ring } from './Ring'
import { getDashboard, DashboardData } from '../api'

export function StatusRail() {
  const [data, setData] = useState<DashboardData | null>(null)

  useEffect(() => {
    getDashboard().then(setData).catch(() => {})
    const id = setInterval(() => getDashboard().then(setData).catch(() => {}), 30000)
    return () => clearInterval(id)
  }, [])

  const pressure = data?.deadline_pressure?.score ?? 0
  const workload = data?.workload_density?.score ?? 0
  const completionPct = data?.fitness?.completion_pct ?? 0
  const syncHealth = data?.sync_health ?? {}

  const syncStatus = (s: string) => {
    if (s === 'ok' || s === 'success') return 'ok'
    if (s === 'error' || s === 'failed') return 'err'
    return 'warn'
  }

  return (
    <aside className="status-rail">
      <div className="rail-avatar">
        <div className="emoji">🧠</div>
        <div className="name">Cognitive OS</div>
      </div>

      <div className="rail-ring-group">
        <div className="rail-ring">
          <Ring pct={pressure * 100} color="pink" />
          <div>
            <div className="ring-label">压力</div>
            <div className="ring-value">{(pressure * 100).toFixed(0)}%</div>
          </div>
        </div>
        <div className="rail-ring">
          <Ring pct={workload * 100} color="gold" />
          <div>
            <div className="ring-label">负荷</div>
            <div className="ring-value">{(workload * 100).toFixed(0)}%</div>
          </div>
        </div>
        <div className="rail-ring">
          <Ring pct={completionPct} color="green" />
          <div>
            <div className="ring-label">训练</div>
            <div className="ring-value">{completionPct}%</div>
          </div>
        </div>
      </div>

      <div className="rail-pills">
        {Object.entries(syncHealth).map(([key, val]: [string, any]) => (
          <div className="rail-pill" key={key}>
            <span className="label">{key}</span>
            <span className={`status ${syncStatus(val?.status)}`}>
              {val?.status || '?'}
            </span>
          </div>
        ))}
      </div>
    </aside>
  )
}

import React, { useEffect, useState } from 'react'
import {
  getDashboard, getRecentActions, postUndo, postSystemAction,
  DashboardData, RecentAction, SystemAction,
} from '../api'
import { announceWebAction, refreshDashboard } from '../events'

interface Props { onAction?: (text: string, action?: string) => void }

const STATUS_LABELS: Record<string, string> = {
  ok: '正常',
  success: '正常',
  unknown: '未知',
  error: '异常',
  failed: '失败',
}

const STATUS_CLASS: Record<string, string> = {
  ok: 'ok',
  success: 'ok',
  unknown: 'warn',
  error: 'err',
  failed: 'err',
}

export function SystemPage({ onAction }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [recentActions, setRecentActions] = useState<RecentAction[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [undoingAction, setUndoingAction] = useState('')
  const [actionStatus, setActionStatus] = useState('')
  const [busySystemAction, setBusySystemAction] = useState('')
  const [systemStatus, setSystemStatus] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const load = () => {
    getDashboard().then(setData).catch(() => {})
    getRecentActions(12).then(res => setRecentActions(res.actions || [])).catch(() => {})
  }

  useEffect(() => {
    load()
    const handler = () => load()
    window.addEventListener('dashboard-refresh', handler)
    return () => window.removeEventListener('dashboard-refresh', handler)
  }, [])

  const handleRefresh = () => {
    setRefreshing(true)
    load()
    setTimeout(() => setRefreshing(false), 1000)
  }

  const handleUndo = async (actionId: string) => {
    setUndoingAction(actionId)
    setActionStatus('')
    try {
      const res = await postUndo(actionId)
      setActionStatus(res.message)
      load()
      if (res.ok) {
        announceWebAction({
          ok: true,
          message: res.message,
          action: 'undo',
          action_type: 'web_undo',
          can_undo: false,
        })
        refreshDashboard()
      }
    } catch (err: any) {
      setActionStatus(err.detail || '撤回失败')
    } finally {
      setUndoingAction('')
      setTimeout(() => setActionStatus(''), 3500)
    }
  }

  const runSystemAction = async (action: SystemAction) => {
    setBusySystemAction(action)
    setSystemStatus(null)
    try {
      const res = await postSystemAction(action)
      if (res.dashboard) setData(res.dashboard)
      setSystemStatus({ type: res.ok ? 'ok' : 'err', text: `${res.message} · ${res.events} 个事件` })
      load()
      announceWebAction({
        ok: res.ok,
        message: `${res.message} · ${res.events} 个事件`,
        action: res.action,
        action_type: `system_${res.action}`,
        can_undo: false,
      })
      refreshDashboard()
    } catch (err: any) {
      setSystemStatus({ type: 'err', text: err.detail || '系统操作失败' })
    } finally {
      setBusySystemAction('')
      setTimeout(() => setSystemStatus(null), 4200)
    }
  }

  if (!data) return <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-dim)' }}>加载中...</div>

  const syncHealth = data.sync_health ?? {}
  const consistency = data.calendar_consistency ?? {}
  const latestReview = consistency.latest ?? {}
  const latestRepair = consistency.repair ?? {}
  const findings = Array.isArray(latestReview.findings) ? latestReview.findings : []

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600 }}>系统</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          {onAction && (
            <button
              onClick={() => onAction('同步刷新数据', 'sync_refresh')}
              style={{
                padding: '6px 16px', borderRadius: 20, border: '1px solid rgba(255,255,255,0.1)',
                background: 'transparent', color: 'var(--accent-green)', cursor: 'pointer', fontSize: 13,
              }}
            >
              同步刷新数据
            </button>
          )}
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            style={{
              padding: '6px 16px', borderRadius: 20, border: '1px solid rgba(255,255,255,0.1)',
              background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 13,
            }}
          >
            {refreshing ? '刷新中...' : '刷新'}
          </button>
        </div>
      </div>

      <div className="system-control-grid">
        <div className="card system-control-card">
          <div className="system-control-head">
            <div>
              <div className="system-kicker">数据刷新</div>
              <h3>同步控制</h3>
            </div>
            <span className="system-muted">通过 runtime 事件触发</span>
          </div>
          <div className="system-button-grid">
            <SystemButton action="sync_all" label="全部刷新" busy={busySystemAction} onClick={runSystemAction} primary />
            <SystemButton action="sync_schedule" label="同步课表" busy={busySystemAction} onClick={runSystemAction} />
            <SystemButton action="sync_homework" label="同步作业" busy={busySystemAction} onClick={runSystemAction} />
            <SystemButton action="sync_calendar" label="同步日历" busy={busySystemAction} onClick={runSystemAction} />
            <SystemButton action="sync_vocab" label="同步背词" busy={busySystemAction} onClick={runSystemAction} />
          </div>
        </div>

        <div className="card system-control-card">
          <div className="system-control-head">
            <div>
              <div className="system-kicker">日历一致性</div>
              <h3>审查与修正</h3>
            </div>
            <span className={`system-pill ${severityClass(latestReview.overall_severity)}`}>
              {severityLabel(latestReview.overall_severity)}
            </span>
          </div>
          <div className="system-consistency-summary">
            <div className="stat-row">
              <span className="label">最近审查</span>
              <span className="value">{formatTime(latestReview.last_completed_at || latestReview.last_requested_at)}</span>
            </div>
            <div className="stat-row">
              <span className="label">问题数</span>
              <span className="value">{findings.length}</span>
            </div>
            <div className="stat-row">
              <span className="label">最近修正</span>
              <span className="value">{formatTime(latestRepair.last_repair_completed_at || latestRepair.last_repair_requested_at)}</span>
            </div>
          </div>
          <div className="system-button-grid two">
            <SystemButton action="calendar_review" label="审查日历" busy={busySystemAction} onClick={runSystemAction} />
            <SystemButton action="calendar_repair" label="修正日历" busy={busySystemAction} onClick={runSystemAction} danger />
          </div>
        </div>
      </div>

      {systemStatus && (
        <div className={`system-action-status ${systemStatus.type}`}>{systemStatus.text}</div>
      )}

      <div className="section-label" style={{ marginBottom: 8 }}>同步状态</div>
      <div className="sync-grid" style={{ marginBottom: 20 }}>
        {Object.entries(syncHealth).map(([key, val]: [string, any]) => (
          <div className="sync-item" key={key}>
            <div className="name">{key}</div>
            <div className="detail">
              <span className={`status ${STATUS_CLASS[val?.status] || 'warn'}`}>
                {STATUS_LABELS[val?.status] || val?.status || '未知'}
              </span>
              {val?.last_sync && (
                <span style={{ marginLeft: 8 }}>{val.last_sync.slice(0, 16)}</span>
              )}
            </div>
            {val?.error && (
              <div style={{ fontSize: 10, color: 'var(--danger)', marginTop: 2 }}>{val.error}</div>
            )}
          </div>
        ))}
      </div>

      <div className="section-label" style={{ marginBottom: 8 }}>运行时信息</div>
      <div className="card">
        <div className="stat-row">
          <span className="label">数据日期</span>
          <span className="value">{data.today}</span>
        </div>
        <div className="stat-row">
          <span className="label">活跃课程数</span>
          <span className="value">{data.active_context?.active_course_count ?? '?'}</span>
        </div>
        <div className="stat-row">
          <span className="label">作业列表</span>
          <span className="value">{data.homework_count} 项</span>
        </div>
      </div>

      <div className="section-label" style={{ margin: '20px 0 8px' }}>最近操作</div>
      <div className="card recent-actions-card">
        {recentActions.length > 0 ? (
          recentActions.map(action => (
            <div className="recent-action-row" key={action.action_id}>
              <div className="recent-action-main">
                <div className="recent-action-title">
                  {action.label}
                  {action.reverted && <span className="recent-action-badge">已撤回</span>}
                </div>
                <div className="recent-action-meta">
                  {formatTime(action.timestamp)} · {action.action_type}
                </div>
              </div>
              <button
                className="recent-action-undo"
                disabled={!action.can_undo || undoingAction === action.action_id}
                onClick={() => handleUndo(action.action_id)}
              >
                {undoingAction === action.action_id ? '撤回中' : '撤回'}
              </button>
            </div>
          ))
        ) : (
          <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            暂无可显示的 Web 操作。通过财务记录或全局输入触发动作后会出现在这里。
          </div>
        )}
        {actionStatus && <div className="recent-action-status">{actionStatus}</div>}
      </div>
    </div>
  )
}

function SystemButton({
  action,
  label,
  busy,
  onClick,
  primary = false,
  danger = false,
}: {
  action: SystemAction;
  label: string;
  busy: string;
  onClick: (action: SystemAction) => void;
  primary?: boolean;
  danger?: boolean;
}) {
  const disabled = Boolean(busy)
  return (
    <button
      className={`system-action-btn${primary ? ' primary' : ''}${danger ? ' danger' : ''}`}
      disabled={disabled}
      onClick={() => onClick(action)}
    >
      {busy === action ? '处理中...' : label}
    </button>
  )
}

function severityLabel(value?: string) {
  const v = value || 'unknown'
  const labels: Record<string, string> = {
    ok: '正常',
    info: '提示',
    warning: '注意',
    warn: '注意',
    error: '异常',
    critical: '严重',
    unknown: '未知',
  }
  return labels[v] || v
}

function severityClass(value?: string) {
  const v = value || 'unknown'
  if (['ok', 'info'].includes(v)) return 'ok'
  if (['error', 'critical'].includes(v)) return 'err'
  return 'warn'
}

function formatTime(value?: string) {
  if (!value) return '时间未知'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.slice(0, 16)
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

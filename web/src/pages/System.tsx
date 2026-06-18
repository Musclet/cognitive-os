import React, { useEffect, useState } from 'react'
import {
  getDashboard, getRecentActions, postUndo, postSystemAction, getWebStatus, syncGoogleCalendar,
  getGoogleCalendarDiagnostics,
  DashboardData, RecentAction, SystemAction, WebStatus, GoogleCalendarDiagnostics,
} from '../api'
import { announceWebAction, refreshDashboard } from '../events'

interface Props { onAction?: (text: string, action?: string) => void }

const STATUS_LABELS: Record<string, string> = {
  ok: '正常',
  success: '正常',
  unknown: '未知',
  error: '异常',
  failed: '失败',
  completed: '已完成',
  running: '同步中',
  mock: 'Mock 模式',
}

const STATUS_CLASS: Record<string, string> = {
  ok: 'ok',
  success: 'ok',
  unknown: 'warn',
  error: 'err',
  failed: 'err',
  completed: 'ok',
  running: 'warn',
  mock: 'warn',
}

function chaoxingSyncText(status: any): string {
  const state = status?.mock_enabled
    ? 'Mock 模式'
    : (STATUS_LABELS[status?.status] || status?.status || '未知')
  const count = status?.pulled_count ?? status?.homework_count ?? 0
  const error = status?.error_code
    ? ` · ${status.error_code}${status.error ? `: ${status.error}` : ''}`
    : ''
  return `超星作业：${state} · 拉取 ${count} 项${error}`
}

function jwxtSyncText(status: any): string {
  const state = STATUS_LABELS[status?.status] || status?.status || '未知'
  const pulledCount = status?.pulled_count ?? 0
  const blockCount = status?.temporal_blocks_count ?? 0
  const error = status?.error_code
    ? ` · ${status.error_code}${status.error ? `: ${status.error}` : ''}`
    : ''
  return `教务课表：${state} · 拉取 ${pulledCount} 条 · 课程 ${blockCount} 条${error}`
}

export function SystemPage({ onAction }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [recentActions, setRecentActions] = useState<RecentAction[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [undoingAction, setUndoingAction] = useState('')
  const [actionStatus, setActionStatus] = useState('')
  const [busySystemAction, setBusySystemAction] = useState('')
  const [systemStatus, setSystemStatus] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [webStatus, setWebStatus] = useState<WebStatus | null>(null)
  const [webStatusError, setWebStatusError] = useState('')
  const [syncingCalendar, setSyncingCalendar] = useState(false)
  const [syncCalResult, setSyncCalResult] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [gcalDiag, setGcalDiag] = useState<GoogleCalendarDiagnostics | null>(null)
  const [gcalDiagErr, setGcalDiagErr] = useState('')

  const load = () => {
    getDashboard().then(setData).catch(() => {})
    getRecentActions(12).then(res => setRecentActions(res.actions || [])).catch(() => {})
    getWebStatus()
      .then(s => { setWebStatus(s); setWebStatusError('') })
      .catch((err: any) => { setWebStatusError(err.detail || err.message || '状态获取失败') })
    getGoogleCalendarDiagnostics()
      .then(d => { setGcalDiag(d); setGcalDiagErr('') })
      .catch((err: any) => { setGcalDiagErr(err.detail || err.message || '诊断获取失败') })
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
      let dashboard = res.dashboard
      let syncStatus = res.sync_status
      if (action === 'sync_homework' && syncStatus?.status === 'running') {
        for (let attempt = 0; attempt < 30; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 2000))
          dashboard = await getDashboard()
          const health = dashboard.sync_health?.chaoxing
          syncStatus = {
            status: health?.status || 'running',
            error_code: health?.error_code,
            error: health?.error,
            mock_enabled: health?.mock_enabled,
            pulled_count: health?.pulled_count ?? health?.count,
            homework_count: dashboard.homework_count,
            last_sync_at: health?.last_sync,
          }
          if (
            syncStatus.status === 'completed'
            || syncStatus.status === 'failed'
            || syncStatus.mock_enabled
          ) break
        }
      }
      if (dashboard) setData(dashboard)
      const syncFailed = syncStatus?.status === 'failed' || Boolean(syncStatus?.mock_enabled)
      const statusText = action === 'sync_homework' && syncStatus
        ? chaoxingSyncText(syncStatus)
        : action === 'sync_schedule' && syncStatus
          ? jwxtSyncText(syncStatus)
        : `${res.message} · ${res.events} 个事件`
      setSystemStatus({ type: syncFailed || !res.ok ? 'err' : 'ok', text: statusText })
      load()
      announceWebAction({
        ok: res.ok && !syncFailed,
        message: statusText,
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

  const handleSyncCalendar = async () => {
    setSyncingCalendar(true)
    setSyncCalResult(null)
    try {
      const res = await syncGoogleCalendar()
      setSyncCalResult({ type: res.ok ? 'ok' : 'err', text: res.message })
      load()
    } catch (err: any) {
      setSyncCalResult({ type: 'err', text: err.detail || err.message || '同步请求失败' })
    } finally {
      setSyncingCalendar(false)
      setTimeout(() => setSyncCalResult(null), 5000)
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
          <div style={{ marginTop: 8, borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 8 }}>
            <button
              className="system-action-btn primary"
              disabled={syncingCalendar}
              onClick={handleSyncCalendar}
              style={{ width: '100%' }}
            >
              {syncingCalendar ? '同步中...' : '同步 Google Calendar'}
            </button>
            {syncCalResult && (
              <div style={{
                marginTop: 6, fontSize: 12, fontWeight: 600,
                color: syncCalResult.type === 'ok' ? 'var(--accent-green)' : 'var(--danger)',
              }}>
                {syncCalResult.text}
              </div>
            )}
          </div>
        </div>

        <div className="card system-control-card">
          <div className="system-control-head">
            <div>
              <div className="system-kicker">Google Calendar 诊断</div>
              <h3>同步就绪检查</h3>
            </div>
            <span className={`system-pill ${gcalDiag?.ready_for_real_sync ? 'ok' : (gcalDiag ? 'warn' : 'warn')}`}>
              {gcalDiag ? (gcalDiag.ready_for_real_sync ? '就绪' : '未就绪') : '...'}
            </span>
          </div>
          {gcalDiagErr && (
            <div style={{ fontSize: 12, color: 'var(--danger)', marginBottom: 8 }}>{gcalDiagErr}</div>
          )}
          {gcalDiag ? (
            <div style={{ fontSize: 12 }}>
              <div className="stat-row">
                <span className="label">Mock 模式</span>
                <span className="value">{gcalDiag.mock ? '开启' : '关闭'}</span>
              </div>
              <div className="stat-row">
                <span className="label">写入</span>
                <span className="value">{gcalDiag.write_enabled ? '启用' : '停用'}</span>
              </div>
              <div className="stat-row">
                <span className="label">Credentials 文件</span>
                <span className="value" style={{ color: gcalDiag.credentials_file_exists ? 'var(--accent-green)' : 'var(--text-dim)' }}>
                  {gcalDiag.credentials_file_exists ? '存在' : gcalDiag.credentials_path_configured ? '缺失' : '未配置'}
                </span>
              </div>
              <div className="stat-row">
                <span className="label">Credentials ENV</span>
                <span className="value" style={{ color: gcalDiag.credentials_env_configured ? 'var(--accent-green)' : 'var(--text-dim)' }}>
                  {gcalDiag.credentials_env_configured ? '已配置' : '未配置'}
                </span>
              </div>
              <div className="stat-row">
                <span className="label">Token 文件</span>
                <span className="value" style={{ color: gcalDiag.token_file_exists ? 'var(--accent-green)' : 'var(--text-dim)' }}>
                  {gcalDiag.token_file_exists ? '存在' : gcalDiag.token_path_configured ? '缺失' : '未配置'}
                </span>
              </div>
              <div className="stat-row">
                <span className="label">Token ENV</span>
                <span className="value" style={{ color: gcalDiag.token_env_configured ? 'var(--accent-green)' : 'var(--text-dim)' }}>
                  {gcalDiag.token_env_configured ? '已配置' : '未配置'}
                </span>
              </div>
              <div className="stat-row">
                <span className="label">时区</span>
                <span className="value">{gcalDiag.timezone}</span>
              </div>
              {gcalDiag.missing.length > 0 && (
                <div style={{ marginTop: 8, color: 'var(--accent-gold)', fontSize: 11 }}>
                  缺少: {gcalDiag.missing.join(', ')}
                </div>
              )}
            </div>
          ) : (
            !gcalDiagErr && <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>加载中...</div>
          )}
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
                {val?.mock_enabled ? 'Mock 模式' : (STATUS_LABELS[val?.status] || val?.status || '未知')}
              </span>
              {val?.last_sync && (
                <span style={{ marginLeft: 8 }}>{val.last_sync.slice(0, 16)}</span>
              )}
              {(val?.pulled_count != null || val?.homework_count != null) && (
                <span style={{ marginLeft: 8 }}>
                  拉取 {val.pulled_count ?? val.homework_count ?? 0} 项
                </span>
              )}
              {val?.temporal_blocks_count != null && (
                <span style={{ marginLeft: 8 }}>
                  课程 {val.temporal_blocks_count} 条
                </span>
              )}
            </div>
            {val?.error_code && (
              <div style={{ fontSize: 10, color: 'var(--danger)', marginTop: 2 }}>{val.error_code}</div>
            )}
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

      <div className="section-label" style={{ margin: '20px 0 8px' }}>系统状态</div>
      {webStatusError && (
        <div className="system-action-status err" style={{ marginBottom: 12 }}>{webStatusError}</div>
      )}
      {webStatus ? (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="stat-row">
            <span className="label">事件总数</span>
            <span className="value">{webStatus.event_count}</span>
          </div>
          <div className="stat-row">
            <span className="label">StateEngine 事件</span>
            <span className="value">{webStatus.state_event_count}</span>
          </div>
          <div className="stat-row">
            <span className="label">State Hash</span>
            <span className="value" style={{ fontFamily: 'monospace', fontSize: 11 }}>
              {webStatus.state_hash ? webStatus.state_hash.slice(0, 8) : '—'}…
            </span>
          </div>
          <div className="stat-row">
            <span className="label">Bus 订阅类型</span>
            <span className="value">{Object.keys(webStatus.bus_subscribers || {}).length}</span>
          </div>
          <div className="stat-row">
            <span className="label">数据库类型</span>
            <span className="value">{webStatus.settings.database_url_type}</span>
          </div>
          <div style={{ marginTop: 10, borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 10 }}>
            <div className="stat-row">
              <span className="label">超星 Mock</span>
              <span className="value">{webStatus.settings.chaoxing_mock ? '是' : '否'}</span>
            </div>
            <div className="stat-row">
              <span className="label">超星 State</span>
              <span className="value">
                {!webStatus.settings.chaoxing_state_file_configured
                  ? '未配置'
                  : webStatus.settings.chaoxing_state_file_exists ? '已就绪' : '文件缺失'}
              </span>
            </div>
            <div className="stat-row">
              <span className="label">教务 Mock</span>
              <span className="value">{webStatus.settings.jwxt_mock ? '是' : '否'}</span>
            </div>
            <div className="stat-row">
              <span className="label">日历 Mock</span>
              <span className="value">{webStatus.settings.google_calendar_mock ? '是' : '否'}</span>
            </div>
            <div className="stat-row">
              <span className="label">Momo 同步</span>
              <span className="value">{webStatus.settings.momo_sync_enabled ? '启用' : '停用'}</span>
            </div>
            <div className="stat-row">
              <span className="label">Obsidian</span>
              <span className="value">{webStatus.settings.obsidian_vault_configured ? '已配置' : '未配置'}</span>
            </div>
          </div>
          {webStatus.worker && (
            <div style={{ marginTop: 10, borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 10 }}>
              <div className="stat-row">
                <span className="label">Worker 状态</span>
                <span className="value" style={{
                  color: webStatus.worker.status === 'alive' ? 'var(--accent-green)'
                       : webStatus.worker.status === 'stale' ? 'var(--accent-gold)'
                       : 'var(--text-dim)'
                }}>
                  {webStatus.worker.status === 'alive' ? '活跃'
                   : webStatus.worker.status === 'stale' ? '延迟'
                   : '离线'}
                </span>
              </div>
              {webStatus.worker.last_heartbeat && (
                <div className="stat-row">
                  <span className="label">最后心跳</span>
                  <span className="value" style={{ fontSize: 11 }}>
                    {new Date(webStatus.worker.last_heartbeat).toLocaleString('zh-CN', {
                      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
                    })}
                    {webStatus.worker.seconds_since_heartbeat != null && (
                      <span style={{ marginLeft: 6, color: 'var(--text-dim)' }}>
                        ({webStatus.worker.seconds_since_heartbeat}s 前)
                      </span>
                    )}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        !webStatusError && (
          <div style={{ fontSize: 13, color: 'var(--text-dim)', padding: '16px 0' }}>加载中...</div>
        )
      )}

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

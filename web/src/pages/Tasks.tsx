import React, { useEffect, useState } from 'react'
import { getDashboard, DashboardData, postTasksAction, postProposalDecision, TasksActionRequest } from '../api'
import { announceWebAction, refreshDashboard } from '../events'

interface Props { onAction?: (text: string, action?: string, payload?: Record<string, any>) => void }

const STATUS_COLORS: Record<string, string> = {
  pending: 'var(--accent-gold)',
  submitted: 'var(--accent-green)',
  expired: 'var(--danger)',
  delayed: 'var(--accent-pink)',
}

const STATUS_LABELS: Record<string, string> = {
  pending: '待完成',
  submitted: '已提交',
  expired: '已过期',
  delayed: '稍后',
}

function homeworkEmptyText(reason?: string): string {
  const messages: Record<string, string> = {
    homework_empty_mock_filtered: '作业为空：当前为 mock 模式且示例课程被过滤',
    homework_empty_mock_enabled: '作业为空：当前为 mock 模式',
    chaoxing_state_file_missing: '作业为空：超星未配置真实登录状态',
    chaoxing_auth_missing: '作业为空：超星未配置真实登录状态',
    chaoxing_session_expired: '作业同步失败：超星登录状态失效',
    chaoxing_auth_failed: '作业同步失败：超星认证失败',
    chaoxing_playwright_missing: '作业同步失败：Playwright 不可用',
    chaoxing_browser_unavailable: '作业同步失败：浏览器不可用',
  }
  return messages[reason || ''] || '暂无作业'
}

export function Tasks({ onAction }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [completionText, setCompletionText] = useState('')
  const [completionStatus, setCompletionStatus] = useState('')
  const [taskStatus, setTaskStatus] = useState<Record<string, string>>({})

  // Multi-select state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [batchBusy, setBatchBusy] = useState(false)
  const [batchMsg, setBatchMsg] = useState('')

  // Calendar proposal state
  const [calFormHw, setCalFormHw] = useState<any | null>(null)
  const [calForm, setCalForm] = useState({ date: '', start: '', end: '', location: '', note: '' })
  const [calProposal, setCalProposal] = useState<any | null>(null)
  const [calBusy, setCalBusy] = useState(false)
  const [decidingCal, setDecidingCal] = useState<'accept' | 'reject' | null>(null)

  const loadDashboard = () => {
    getDashboard().then(setData).catch(() => {})
  }

  useEffect(() => {
    loadDashboard()
    const handler = () => loadDashboard()
    window.addEventListener('dashboard-refresh', handler)
    return () => window.removeEventListener('dashboard-refresh', handler)
  }, [])

  const handleCompletionSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!completionText.trim() || !onAction) return
    onAction(completionText.trim())
    setCompletionStatus('已发送')
    setCompletionText('')
    setTimeout(() => setCompletionStatus(''), 3000)
  }

  const runHomeworkAction = (hw: any, action: 'complete_homework' | 'delay_homework_30' | 'skip_homework') => {
    if (!onAction) return
    const id = String(hw.id || hw.title || '作业')
    const title = String(hw.title || '作业')
    const payload = {
      task_id: id,
      homework_id: id,
      title,
      text: title,
      course: hw.course || '',
      deadline: hw.deadline || '',
      delay_minutes: action === 'delay_homework_30' ? 30 : undefined,
    }
    const textByAction: Record<typeof action, string> = {
      complete_homework: `完成了 ${title}`,
      delay_homework_30: `${title} 稍后30分钟`,
      skip_homework: `跳过 ${title}`,
    }
    const statusByAction: Record<typeof action, string> = {
      complete_homework: '已记录完成',
      delay_homework_30: '30分钟后再处理',
      skip_homework: '已跳过',
    }
    setTaskStatus(prev => ({ ...prev, [id]: statusByAction[action] }))
    onAction(textByAction[action], action, payload)
    setTimeout(() => {
      setTaskStatus(prev => {
        const next = { ...prev }
        delete next[id]
        return next
      })
    }, 3500)
  }

  // ── Multi-select helpers ──────────────────────────────────────────────

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    const homework = data?.homework ?? []
    if (selectedIds.size === homework.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(homework.map((hw: any) => String(hw.id || hw.title || ''))))
    }
  }

  const clearSelection = () => {
    setSelectedIds(new Set())
  }

  const selectedHomeworkList = () => {
    const homework = data?.homework ?? []
    return homework
      .filter((hw: any) => selectedIds.has(String(hw.id || hw.title || '')))
      .map((hw: any) => ({
        id: String(hw.id || hw.title || ''),
        title: String(hw.title || ''),
        course: hw.course || '',
        deadline: hw.deadline || '',
      }))
  }

  // ── Batch actions ─────────────────────────────────────────────────────

  const doBatchAction = async (action: TasksActionRequest['action']) => {
    const items = selectedHomeworkList()
    if (items.length === 0) return
    setBatchBusy(true)
    setBatchMsg('')
    try {
      const res = await postTasksAction({ action, items })
      if (res.ok) {
        loadDashboard()
        refreshDashboard()
        clearSelection()
      }
      announceWebAction({
        ok: res.ok,
        message: res.message,
        action: res.action,
        action_id: res.action_id || null,
        action_type: `tasks_${res.action}`,
        can_undo: false,
      })
      setBatchMsg(res.message)
    } catch (err: any) {
      setBatchMsg(err.detail || '操作失败')
    } finally {
      setBatchBusy(false)
      setTimeout(() => setBatchMsg(''), 3000)
    }
  }

  // ── Calendar proposal ─────────────────────────────────────────────────

  const openCalForm = (hw: any) => {
    setCalFormHw(hw)
    const defaults = defaultCalendarFormTime()
    setCalForm({
      date: defaults.date,
      start: defaults.start,
      end: defaults.end,
      location: '',
      note: '',
    })
    setCalProposal(null)
  }

  const closeCalForm = () => {
    setCalFormHw(null)
    setCalProposal(null)
  }

  const submitCalProposal = async () => {
    if (!calFormHw) return
    setCalBusy(true)
    try {
      const items = [{
        id: String(calFormHw.id || calFormHw.title || ''),
        title: String(calFormHw.title || ''),
        course: calFormHw.course || '',
      }]
      const res = await postTasksAction({
        action: 'calendar_proposal',
        items,
        date: calForm.date,
        start_time: calForm.start,
        end_time: calForm.end || undefined,
        location: calForm.location || undefined,
        note: calForm.note || undefined,
      })
      if (res.ok && res.proposal) {
        setCalProposal(res.proposal)
        loadDashboard()
        announceWebAction({
          ok: true,
          message: res.message,
          action: res.action,
          action_type: 'tasks_calendar_proposal',
          can_undo: false,
        })
        refreshDashboard()
      } else {
        announceWebAction({
          ok: res.ok,
          message: res.message,
          action: res.action,
          action_type: 'tasks_calendar_proposal',
          can_undo: false,
        })
        setBatchMsg(res.message)
      }
    } catch (err: any) {
      setBatchMsg(err.detail || '日历提案失败')
    } finally {
      setCalBusy(false)
      setTimeout(() => setBatchMsg(''), 3000)
    }
  }

  const handleProposalDecision = async (decision: 'accept' | 'reject') => {
    if (!calProposal) return
    setDecidingCal(decision)
    try {
      const res = await postProposalDecision(calProposal, decision)
      if (res.ok || decision === 'reject') {
        setCalProposal(null)
        setCalFormHw(null)
      }
      setBatchMsg(res.message)
      loadDashboard()
      announceWebAction({
        ok: res.ok,
        message: res.message,
        action: `calendar_${decision}`,
        action_type: 'tasks_calendar_proposal_decision',
        can_undo: false,
      })
      refreshDashboard()
    } catch (err: any) {
      setBatchMsg(err.detail || '提案处理失败')
    } finally {
      setDecidingCal(null)
      setTimeout(() => setBatchMsg(''), 3000)
    }
  }

  if (!data) return <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-dim)' }}>加载中...</div>

  const homework = data.homework ?? []
  const hiddenCount = data.homework_hidden_count ?? 0
  const vocab = data.vocab_progress ?? {}
  const art = data.art
  const allSelected = homework.length > 0 && selectedIds.size === homework.length

  return (
    <div>
      <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>任务</h2>

      {onAction && (
        <div className="cmd-page-actions" style={{ marginBottom: 16 }}>
          <button className="quick-chip" onClick={() => onAction('同步作业', 'sync_refresh')}>
            同步作业
          </button>
          <button className="quick-chip" onClick={() => onAction('刷新状态', 'sync_refresh')}>
            刷新状态
          </button>
        </div>
      )}

      {/* Completion record form */}
      {onAction && (
        <form onSubmit={handleCompletionSubmit} className="cmd-inline-form" style={{ marginBottom: 16 }}>
          <input
            type="text"
            className="cmd-composer-input"
            placeholder="记录完成事项，如：完成了作业 / 完成了0.5h画画..."
            value={completionText}
            onChange={e => setCompletionText(e.target.value)}
          />
          <button type="submit" className="cmd-composer-btn" disabled={!completionText.trim()}>记录完成</button>
          {completionStatus && <span className="cmd-status-hint">{completionStatus}</span>}
        </form>
      )}

      {/* Batch status toast */}
      {batchMsg && (
        <div style={{ fontSize: 12, color: 'var(--accent-green)', marginBottom: 8 }}>{batchMsg}</div>
      )}

      {/* Batch toolbar */}
      {selectedIds.size > 0 && (
        <div className="task-batch-toolbar">
          <span className="task-batch-count">已选 {selectedIds.size} 项</span>
          <button className="task-action-btn primary" onClick={() => doBatchAction('complete')} disabled={batchBusy}>
            已完成
          </button>
          <button className="task-action-btn" onClick={() => doBatchAction('delay_30')} disabled={batchBusy}>
            稍后30分钟
          </button>
          <button className="task-action-btn danger" onClick={() => doBatchAction('skip')} disabled={batchBusy}>
            跳过
          </button>
          <button className="task-action-btn" onClick={clearSelection} disabled={batchBusy}>
            清空选择
          </button>
        </div>
      )}

      <div style={{ marginBottom: 20 }}>
        <div className="section-label" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {homework.length > 0 && (
            <input
              type="checkbox"
              className="task-checkbox-all"
              checked={allSelected}
              onChange={toggleSelectAll}
              title="全选/取消"
            />
          )}
          <span>📝 作业 · {homework.length} 项</span>
          {hiddenCount > 0 ? ` · 已处理隐藏 ${hiddenCount} 项` : ''}
        </div>
        {homework.length === 0 && (
          <div className="card" style={{ color: 'var(--text-dim)', fontSize: 13 }}>
            {homeworkEmptyText(data.homework_empty_reason)}
          </div>
        )}
        {homework.map((hw: any, i: number) => {
          const hwId = String(hw.id || hw.title || i)
          const isSelected = selectedIds.has(hwId)
          return (
            <div
              className={`card task-card${isSelected ? ' task-card-selected' : ''}`}
              key={hwId}
              style={{ marginBottom: 8 }}
            >
              <div className="task-card-head">
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flex: 1 }}>
                  <input
                    type="checkbox"
                    className="task-checkbox"
                    checked={isSelected}
                    onChange={() => toggleSelect(hwId)}
                  />
                  <div>
                    <div className="task-title">{hw.title}</div>
                    <div className="task-subtitle">{hw.course || '未归属课程'}</div>
                  </div>
                </div>
                <span className="task-status" style={{ color: STATUS_COLORS[hw.status] || 'var(--text-secondary)' }}>
                  {STATUS_LABELS[hw.status] || hw.status || 'pending'}
                </span>
              </div>
              <div className="task-meta-line">
                {hw.deadline ? `截止 ${hw.deadline.slice(0, 16).replace('T', ' ')}` : '无期限'}
                {hw.feedback?.delayed_until ? ` · 稍后至 ${formatDelayUntil(hw.feedback.delayed_until)}` : ''}
              </div>
              {onAction && (
                <div className="task-actions">
                  <button className="task-action-btn primary" onClick={() => runHomeworkAction(hw, 'complete_homework')}>
                    已完成
                  </button>
                  <button className="task-action-btn" onClick={() => runHomeworkAction(hw, 'delay_homework_30')}>
                    稍后30分钟
                  </button>
                  <button className="task-action-btn danger" onClick={() => runHomeworkAction(hw, 'skip_homework')}>
                    跳过
                  </button>
                  <button className="task-action-btn" onClick={() => openCalForm(hw)}>
                    安排到日历
                  </button>
                </div>
              )}
              {taskStatus[hwId] && (
                <div className="task-local-status">{taskStatus[hwId]}</div>
              )}

              {/* Inline calendar form */}
              {calFormHw && calFormHw.id === hw.id && !calProposal && (
                <div className="task-cal-form">
                  <div className="task-cal-form-row">
                    <label className="task-cal-label">日期</label>
                    <input
                      type="date"
                      className="tl-form-input"
                      value={calForm.date}
                      onChange={e => setCalForm(prev => ({ ...prev, date: e.target.value }))}
                    />
                  </div>
                  <div className="task-cal-form-row">
                    <label className="task-cal-label">开始</label>
                    <input
                      type="time"
                      className="tl-form-input"
                      value={calForm.start}
                      onChange={e => setCalForm(prev => ({ ...prev, start: e.target.value }))}
                    />
                  </div>
                  <div className="task-cal-form-row">
                    <label className="task-cal-label">结束</label>
                    <input
                      type="time"
                      className="tl-form-input"
                      value={calForm.end}
                      onChange={e => setCalForm(prev => ({ ...prev, end: e.target.value }))}
                    />
                  </div>
                  <div className="task-cal-form-row">
                    <label className="task-cal-label">地点</label>
                    <input
                      type="text"
                      className="tl-form-input"
                      placeholder="可选"
                      value={calForm.location}
                      onChange={e => setCalForm(prev => ({ ...prev, location: e.target.value }))}
                    />
                  </div>
                  <div className="task-cal-form-row">
                    <label className="task-cal-label">备注</label>
                    <input
                      type="text"
                      className="tl-form-input"
                      placeholder="可选"
                      value={calForm.note}
                      onChange={e => setCalForm(prev => ({ ...prev, note: e.target.value }))}
                    />
                  </div>
                  <div className="task-cal-form-actions">
                    <button
                      className="task-action-btn primary"
                      onClick={submitCalProposal}
                      disabled={calBusy || !calForm.date || !calForm.start}
                    >
                      {calBusy ? '创建中...' : '生成提案'}
                    </button>
                    <button className="task-action-btn danger" onClick={closeCalForm}>
                      取消
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Inline proposal card */}
      {calProposal && (
        <div className="task-proposal-card">
          <div className="task-proposal-inner">
            <div className="cmd-card-kicker">日历提案</div>
            <div className="cmd-card-title">
              {calProposal.action_payload?.title || '作业安排'}
            </div>
            <div className="cmd-card-meta">
              {formatProposalTime(calProposal.action_payload?.start)}
              {calProposal.action_payload?.end ? ` - ${formatProposalTime(calProposal.action_payload.end)}` : ''}
              {calProposal.action_payload?.location ? ` · ${calProposal.action_payload.location}` : ''}
            </div>
            <div className="cmd-card-note">尚未写入 Google Calendar。确认后才会创建日程。</div>
            <div className="cmd-card-actions" style={{ marginTop: 10 }}>
              <button
                className="cmd-mini-btn primary"
                onClick={() => handleProposalDecision('accept')}
                disabled={decidingCal !== null}
              >
                {decidingCal === 'accept' ? '写入中' : '接受并写入日历'}
              </button>
              <button
                className="cmd-mini-btn danger"
                onClick={() => handleProposalDecision('reject')}
                disabled={decidingCal !== null}
              >
                {decidingCal === 'reject' ? '拒绝中' : '拒绝'}
              </button>
              <button className="cmd-mini-btn" onClick={() => { setCalProposal(null); setCalFormHw(null) }} disabled={decidingCal !== null}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}

      {Object.keys(vocab).length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div className="section-label">📖 单词进度</div>
          {Object.entries(vocab).map(([key, v]: [string, any]) => (
            <div className="card" key={key} style={{ marginBottom: 8 }}>
              <div className="stat-row">
                <span className="label">{key}</span>
                <span className="value">{v.total_mastered || 0} 掌握</span>
              </div>
              <div className="stat-row">
                <span className="label">今日新词</span>
                <span className="value">{v.new_words_today || 0}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {art?.planned_minutes != null && (
        <div>
          <div className="section-label">🎨 艺术创作</div>
          <div className="card">
            <div className="stat-row">
              <span className="label">计划</span>
              <span className="value">{art.planned_minutes} 分钟</span>
            </div>
            <div className="stat-row">
              <span className="label">完成</span>
              <span className="value">{art.completed_minutes || 0} 分钟</span>
            </div>
            <div className="stat-row">
              <span className="label">状态</span>
              <span className="value">{art.status || 'pending'}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function formatDelayUntil(value: string) {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function formatProposalTime(value?: string) {
  if (!value) return '时间未解析'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function defaultCalendarFormTime() {
  const now = new Date()
  const rounded = new Date(now)
  rounded.setSeconds(0, 0)
  const minutes = rounded.getMinutes()
  const addMinutes = minutes === 0 ? 0 : 30 - (minutes % 30)
  rounded.setMinutes(minutes + addMinutes)

  const end = new Date(rounded)
  end.setHours(end.getHours() + 1)

  return {
    date: formatLocalDate(rounded),
    start: formatLocalTime(rounded),
    end: formatLocalTime(end),
  }
}

function formatLocalDate(value: Date) {
  const y = value.getFullYear()
  const m = String(value.getMonth() + 1).padStart(2, '0')
  const d = String(value.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function formatLocalTime(value: Date) {
  const h = String(value.getHours()).padStart(2, '0')
  const m = String(value.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}

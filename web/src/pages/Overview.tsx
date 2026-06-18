import React, { useEffect, useState } from 'react'
import {
  getDashboard, DashboardData,
  postTodayAction, postCalendarProposal, postProposalDecision,
  postTasksAction, TasksActionRequest,
} from '../api'
import { Ring } from '../components/Ring'
import { announceWebAction, refreshDashboard } from '../events'

interface Props { onAction?: (text: string, action?: string) => void }

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

export function Overview({ onAction: _onAction }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [dashboardError, setDashboardError] = useState<{ status: number; detail: string } | null>(null)

  // ── Form state ────────────────────────────────────────────────────
  const [compText, setCompText] = useState('')
  const [artMins, setArtMins] = useState('')
  const [artType, setArtType] = useState('练习')
  const [artNote, setArtNote] = useState('')
  const [hydrationCustom, setHydrationCustom] = useState('')
  const [ctxText, setCtxText] = useState('')

  // Calendar proposal form
  const [calTitle, setCalTitle] = useState('')
  const [calDate, setCalDate] = useState(() => defaultCalendarFormTime().date)
  const [calStart, setCalStart] = useState(() => defaultCalendarFormTime().start)
  const [calEnd, setCalEnd] = useState(() => defaultCalendarFormTime().end)
  const [calLoc, setCalLoc] = useState('')
  const [calNote, setCalNote] = useState('')
  const [calProposal, setCalProposal] = useState<any | null>(null)
  const [calDeciding, setCalDeciding] = useState(false)

  const loadDashboard = () => {
    getDashboard()
      .then(d => { setData(d); setDashboardError(null) })
      .catch((err: any) => {
        setDashboardError({
          status: err.status || 0,
          detail: err.detail || err.message || '请求失败',
        })
      })
  }

  useEffect(() => {
    loadDashboard()
    const handler = () => loadDashboard()
    window.addEventListener('dashboard-refresh', handler)
    return () => window.removeEventListener('dashboard-refresh', handler)
  }, [])

  const doAction = async (action: string, payload: Record<string, any>) => {
    setLoading(true)
    setNotice(null)
    try {
      const res = await postTodayAction({ action, ...payload } as any)
      if (res.ok) {
        if (res.dashboard) setData(res.dashboard)
        setNotice({ type: 'ok', text: res.message || '已记录。' })
        announceWebAction({
          ok: true,
          message: res.message || '已记录。',
          action: res.action,
          action_id: res.action_id || null,
          action_type: `today_${res.action}`,
          can_undo: false,
        })
        refreshDashboard()
      }
    } catch (err: any) {
      setNotice({ type: 'err', text: err.detail || '操作失败' })
    } finally {
      setLoading(false)
      window.setTimeout(() => setNotice(null), 2800)
    }
  }

  // ── Calendar proposal ─────────────────────────────────────────────
  const handleCalSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!calTitle || !calDate || !calStart) return
    setLoading(true)
    setNotice(null)
    try {
      const res = await postCalendarProposal({
        action: 'create',
        title: calTitle,
        date: calDate,
        start_time: calStart,
        end_time: calEnd || undefined,
        location: calLoc || undefined,
        note: calNote || undefined,
      })
      if (res.proposal) {
        setCalProposal(res.proposal)
      }
      if (res.ok) {
        setNotice({ type: 'ok', text: res.message || '已创建日历提案。' })
        const defaults = defaultCalendarFormTime()
        setCalTitle(''); setCalDate(defaults.date); setCalStart(defaults.start); setCalEnd(defaults.end)
        setCalLoc(''); setCalNote('')
      }
    } catch (err: any) {
      setNotice({ type: 'err', text: err.detail || '日历提案创建失败' })
    } finally {
      setLoading(false)
      window.setTimeout(() => setNotice(null), 2800)
    }
  }

  const handleCalDecision = async (decision: 'accept' | 'reject') => {
    if (!calProposal) return
    setCalDeciding(true)
    setNotice(null)
    try {
      const res = await postProposalDecision(calProposal, decision)
      if (res.ok || decision === 'reject') {
        setCalProposal(null)
      }
      if (res.dashboard) setData(res.dashboard)
      setNotice({ type: res.ok ? 'ok' : 'err', text: res.message || (decision === 'accept' ? '已写入日历。' : '已拒绝。') })
      announceWebAction({
        ok: res.ok,
        message: res.message || (decision === 'accept' ? '已写入日历。' : '已拒绝。'),
        action: `calendar_${decision}`,
        action_type: 'calendar_proposal_decision',
        can_undo: false,
      })
      refreshDashboard()
    } catch (err: any) {
      setNotice({ type: 'err', text: err.detail || '提案处理失败' })
    } finally {
      setCalDeciding(false)
      window.setTimeout(() => setNotice(null), 2800)
    }
  }

  const runHomeworkAction = async (hw: any, action: TasksActionRequest['action']) => {
    const item = {
      id: String(hw.id || hw.title || ''),
      title: String(hw.title || ''),
      course: hw.course || '',
      deadline: hw.deadline || '',
    }
    if (!item.id || !item.title) return
    setLoading(true)
    setNotice(null)
    try {
      const res = await postTasksAction({ action, items: [item] })
      if (res.dashboard) {
        setData(res.dashboard)
      } else if (res.ok) {
        loadDashboard()
      }
      setNotice({ type: res.ok ? 'ok' : 'err', text: res.message || '已处理。' })
      announceWebAction({
        ok: res.ok,
        message: res.message || '已处理。',
        action: res.action,
        action_id: res.action_id || null,
        action_type: `tasks_${res.action}`,
        can_undo: false,
      })
      if (res.ok) refreshDashboard()
    } catch (err: any) {
      setNotice({ type: 'err', text: err.detail || '作业操作失败' })
    } finally {
      setLoading(false)
      window.setTimeout(() => setNotice(null), 2800)
    }
  }

  if (!data) {
    if (dashboardError) {
      return (
        <div style={{ textAlign: 'center', padding: 60, maxWidth: 400, margin: '0 auto' }}>
          <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 10 }}>
            数据加载失败
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6, fontFamily: 'monospace' }}>
            HTTP {dashboardError.status || '?'}: {dashboardError.detail}
          </div>
          <button
            className="today-btn"
            onClick={() => { setDashboardError(null); loadDashboard() }}
            style={{ marginTop: 8 }}
          >
            重试
          </button>
        </div>
      )
    }
    return <div style={{ color: 'var(--text-dim)', textAlign: 'center', padding: 40 }}>加载中...</div>
  }

  const pendingHw = data.homework_count || 0
  const fitnessPct = data.fitness?.completion_pct ?? 0
  const vocabCount = Object.keys(data.vocab_progress || {}).length
  const financeBudget = data.finance?.monthly_budget ?? 0
  const financeSpend = data.finance?.monthly_spend ?? 0
  const budgetPct = financeBudget > 0 ? Math.min((financeSpend / financeBudget) * 100, 100) : 0

  return (
    <div>
      {/* ── Hero Card ──────────────────────────────────────────── */}
      <div className="hero-card">
        <div className="hero-date">{data.today} {data.weekday}</div>
        <div className="hero-title">今日计划控制台</div>
        <div className="hero-sub">
          {data.active_context?.most_urgent
            ? `紧急: ${data.active_context.most_urgent.course}`
            : '暂无紧急事项'}
        </div>
        <div className="hero-stats">
          <div className="hero-stat">
            <div className="num">{pendingHw}</div>
            <div className="lbl">待办作业</div>
          </div>
          <div className="hero-stat">
            <div className="num">{data.today_schedule?.length || 0}</div>
            <div className="lbl">今日课程</div>
          </div>
          <div className="hero-stat">
            <div className="num">{data.calendar_events?.length || 0}</div>
            <div className="lbl">日历事件</div>
          </div>
        </div>
      </div>

      {/* ── Today Controls ─────────────────────────────────────── */}
      {notice && (
        <div className={`today-notice ${notice.type}`}>{notice.text}</div>
      )}

      {/* ── Today Controls ─────────────────────────────────────── */}
      <div className="today-controls">
        {/* 记录完成 */}
        <div className="today-card">
          <div className="today-card-head">✓ 记录完成</div>
          <form className="today-inline-form" onSubmit={e => { e.preventDefault(); if (compText.trim()) { doAction('completion', { text: compText.trim() }); setCompText('') } }}>
            <input className="today-input" placeholder="完成了什么？" value={compText} onChange={e => setCompText(e.target.value)} />
            <button className="today-btn" disabled={!compText.trim() || loading}>记录</button>
          </form>
        </div>

        {/* 画画进度 */}
        <div className="today-card">
          <div className="today-card-head">🎨 画画进度</div>
          <form className="today-inline-form" onSubmit={e => { e.preventDefault(); const m = parseInt(artMins); if (m > 0) { doAction('art_progress', { minutes: m, type: artType, note: artNote }); setArtMins(''); setArtNote('') } }}>
            <input className="today-input today-input-narrow" type="number" min="1" placeholder="分钟" value={artMins} onChange={e => setArtMins(e.target.value)} />
            <select className="today-select" value={artType} onChange={e => setArtType(e.target.value)}>
              <option value="练习">练习</option>
              <option value="创作">创作</option>
              <option value="临摹">临摹</option>
              <option value="摸鱼">摸鱼</option>
            </select>
            <input className="today-input" placeholder="备注（可选）" value={artNote} onChange={e => setArtNote(e.target.value)} />
            <button className="today-btn" disabled={!artMins || parseInt(artMins) <= 0 || loading}>记录</button>
          </form>
        </div>

        {/* 补水 */}
        <div className="today-card">
          <div className="today-card-head">💧 补水</div>
          <div className="today-inline-form">
            <div className="today-btn-group">
              {[250, 500].map(v => (
                <button key={v} className="today-btn-sm" disabled={loading} onClick={() => doAction('hydration', { amount_ml: v })}>
                  {v}ml
                </button>
              ))}
            </div>
            <form className="today-inline-form" onSubmit={e => { e.preventDefault(); const v = parseInt(hydrationCustom); if (v > 0) { doAction('hydration', { amount_ml: v }); setHydrationCustom('') } }}>
              <input className="today-input today-input-narrow" type="number" min="1" placeholder="自定义" value={hydrationCustom} onChange={e => setHydrationCustom(e.target.value)} />
              <button className="today-btn" disabled={!hydrationCustom || parseInt(hydrationCustom) <= 0 || loading}>补</button>
            </form>
          </div>
        </div>

        {/* 今日状态 */}
        <div className="today-card">
          <div className="today-card-head">🧠 今日状态</div>
          <div className="today-inline-form">
            <form className="today-inline-form" onSubmit={e => { e.preventDefault(); if (ctxText.trim()) { doAction('context', { text: ctxText.trim() }); setCtxText('') } }}>
              <input className="today-input" placeholder="今天感觉如何？" value={ctxText} onChange={e => setCtxText(e.target.value)} />
              <button className="today-btn" disabled={!ctxText.trim() || loading}>记录</button>
            </form>
            <button className="today-btn today-btn-warn" disabled={loading} onClick={() => doAction('context', { text: '今天状态差' })}>
              状态差
            </button>
          </div>
        </div>

        {/* 快速安排到日历 */}
        <div className="today-card">
          <div className="today-card-head">📅 快速安排到日历</div>
          {calProposal ? (
            <div className="today-proposal-card">
              <div className="today-proposal-title">{calProposal.action_payload?.title || '未命名'}</div>
              <div className="today-proposal-time">
                {calProposal.action_payload?.start ? new Date(calProposal.action_payload.start).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : ''}
                {calProposal.action_payload?.end ? ` — ${new Date(calProposal.action_payload.end).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}` : ''}
              </div>
              <div className="today-proposal-note">尚未写入日历，需要确认。</div>
              <div className="today-proposal-actions">
                <button className="today-btn today-btn-primary" onClick={() => handleCalDecision('accept')} disabled={calDeciding}>
                  {calDeciding ? '写入中...' : '接受并写入'}
                </button>
                <button className="today-btn today-btn-warn" onClick={() => handleCalDecision('reject')} disabled={calDeciding}>
                  拒绝
                </button>
              </div>
            </div>
          ) : (
            <form className="today-inline-form today-cal-form" onSubmit={handleCalSubmit}>
              <input className="today-input" placeholder="标题" value={calTitle} onChange={e => setCalTitle(e.target.value)} required />
              <div className="today-cal-row">
                <input className="today-input" type="date" value={calDate} onChange={e => setCalDate(e.target.value)} required />
                <input className="today-input today-input-narrow" type="time" value={calStart} onChange={e => setCalStart(e.target.value)} required />
                <input className="today-input today-input-narrow" type="time" value={calEnd} onChange={e => setCalEnd(e.target.value)} placeholder="结束" />
              </div>
              <div className="today-cal-row">
                <input className="today-input" placeholder="地点（可选）" value={calLoc} onChange={e => setCalLoc(e.target.value)} />
                <input className="today-input" placeholder="备注（可选）" value={calNote} onChange={e => setCalNote(e.target.value)} />
              </div>
              <button className="today-btn" type="submit" disabled={!calTitle || !calDate || !calStart || loading}>创建提案</button>
            </form>
          )}
        </div>

        {/* 同步刷新 */}
        <div className="today-card today-card-action">
          <button className="today-btn today-btn-sync" disabled={loading} onClick={() => doAction('sync_refresh', {})}>
            ↻ 同步刷新
          </button>
        </div>
      </div>

      {/* ── 今日安排 ───────────────────────────────────────────── */}
      <div className="section-label" style={{ marginTop: 20, marginBottom: 10 }}>今日安排</div>
      <div className="cards-grid">
        {data.art?.planned_minutes != null && (
          <div className="card">
            <h3>🎨 艺术创作</h3>
            <div className="stat-row">
              <span className="label">计划</span>
              <span className="value">{data.art.planned_minutes} 分钟</span>
            </div>
            <div className="stat-row">
              <span className="label">完成</span>
              <span className="value">{data.art.completed_minutes || 0} 分钟</span>
            </div>
          </div>
        )}

        <div className="card">
          <h3>📚 课程表</h3>
          {data.today_schedule?.length > 0 ? (
            data.today_schedule.map((s: any, i: number) => (
              <div className="stat-row" key={i}>
                <span className="label">{s.course}</span>
                <span className="value">{s.start}–{s.end}</span>
              </div>
            ))
          ) : (
            <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>今日无课程</div>
          )}
        </div>

        {data.calendar_events?.length > 0 && (
          <div className="card">
            <h3>📅 日历</h3>
            {data.calendar_events.map((e: any, i: number) => (
              <div className="stat-row" key={i}>
                <span className="label">{e.summary}</span>
                <span className="value">{e.start}</span>
              </div>
            ))}
          </div>
        )}

        <div className="card">
          <h3>📝 作业</h3>
          {data.homework?.length > 0 ? (
            <div className="overview-task-list">
              {data.homework.slice(0, 5).map((hw: any, i: number) => (
                <div className="overview-task-item" key={hw.id || hw.title || i}>
                  <div className="overview-task-main">
                    <span className="overview-task-title">{hw.course}: {hw.title}</span>
                    <span className="overview-task-meta">{hw.deadline?.slice(5, 10) || '无期限'}</span>
                  </div>
                  <div className="overview-task-actions">
                    <button disabled={loading} onClick={() => runHomeworkAction(hw, 'complete')}>完成</button>
                    <button disabled={loading} onClick={() => runHomeworkAction(hw, 'delay_30')}>稍后</button>
                    <button disabled={loading} onClick={() => runHomeworkAction(hw, 'skip')}>跳过</button>
                  </div>
                </div>
              ))}
              {data.homework.length > 5 && (
                <div className="overview-task-more">还有 {data.homework.length - 5} 项，去任务页批量处理。</div>
              )}
              </div>
          ) : (
            <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
              {homeworkEmptyText(data.homework_empty_reason)}
            </div>
          )}
        </div>

        <div className="card">
          <h3>💪 健身</h3>
          {data.fitness?.training_day ? (
            <>
              <div className="stat-row">
                <span className="label">训练日</span>
                <span className="value">{data.fitness.training_day}</span>
              </div>
              <div className="stat-row">
                <span className="label">完成</span>
                <span className="value">{data.fitness.completed_sets}/{data.fitness.total_sets}</span>
              </div>
              <Ring pct={fitnessPct} size={48} />
            </>
          ) : (
            <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>今日未训练</div>
          )}
        </div>

        <div className="card">
          <h3>💰 财务</h3>
          <Ring pct={budgetPct} size={48} color="gold" />
          <div className="stat-row">
            <span className="label">已用预算</span>
            <span className="value">¥{financeSpend}/¥{financeBudget}</span>
          </div>
          {data.finance?.savings_target > 0 && (
            <div className="stat-row">
              <span className="label">储蓄</span>
              <span className="value">¥{data.finance.savings_progress || 0}/¥{data.finance.savings_target}</span>
            </div>
          )}
        </div>

        <div className="card">
          <h3>📖 单词</h3>
          {vocabCount > 0 ? (
            <div className="stat-row">
              <span className="label">学习中</span>
              <span className="value">{vocabCount} 组</span>
            </div>
          ) : (
            <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>暂无单词数据</div>
          )}
        </div>
      </div>
    </div>
  )
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

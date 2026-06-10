import React, { useEffect, useState, useCallback } from 'react'
import { getTimeline, postCalendarProposal, postProposalDecision, TimelineData, TimelineEvent, CalendarProposalResponse } from '../api'
import { announceWebAction, refreshDashboard } from '../events'

function addDays(d: Date, n: number): Date {
  const r = new Date(d)
  r.setDate(r.getDate() + n)
  return r
}

function fmtDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function fmtLocalTime(d: Date): string {
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}

function fmtTime(iso: string | undefined): string {
  if (!iso) return ''
  if (iso.includes('T')) return iso.slice(11, 16)
  return iso
}

function todayStr(): string {
  return fmtDate(new Date())
}

function parseDateInput(value: string | undefined): Date {
  if (!value) return new Date()
  const parts = value.split('-').map(Number)
  if (parts.length !== 3 || parts.some(Number.isNaN)) return new Date()
  const [year, month, day] = parts
  return new Date(year, month - 1, day)
}

function parseISODate(iso: string | undefined): string {
  if (!iso) return ''
  return iso.slice(0, 10)
}

const SOURCE_LABELS: Record<string, string> = {
  jwxt: '教务',
  google_calendar: 'Google',
  system: '系统',
  homework: '作业',
}

const CONFLICT_SOURCE_LABELS: Record<string, string> = {
  jwxt: '教务',
  google_calendar: 'Google 日历',
  system: '系统计划',
}

interface ProposalState {
  proposal: any;
  action_label: string;
  conflicts?: Array<{
    source: string;
    type: string;
    title: string;
    start: string;
    end: string;
    location?: string;
    event_id?: string;
  }>;
}

interface EditingEvent {
  event_id: string;
  calendar_id: string;
}

/** 7-day week data: array of {date, label, events} */
interface DayGroup {
  date: string;
  label: string;
  events: TimelineEvent[];
}

function weekLabel(d: Date): string {
  const today = new Date()
  const diff = Math.round((d.getTime() - today.getTime()) / 86400000)
  if (diff === 0) return '今天'
  if (diff === 1) return '明天'
  if (diff === 2) return '后天'
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${d.getMonth() + 1}/${d.getDate()} ${weekdays[d.getDay()]}`
}

export function TimelinePage() {
  const [tab, setTab] = useState<'day' | 'week'>('day')
  const [selectedDate, setSelectedDate] = useState(todayStr())
  const [data, setData] = useState<TimelineData | null>(null)
  const [weekData, setWeekData] = useState<DayGroup[]>([])
  const [showForm, setShowForm] = useState(false)
  const [proposalState, setProposalState] = useState<ProposalState | null>(null)
  const [deciding, setDeciding] = useState<'accept' | 'reject' | null>(null)
  const [toastMsg, setToastMsg] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [editingEvent, setEditingEvent] = useState<EditingEvent | null>(null)

  // Form state
  const [formTitle, setFormTitle] = useState('')
  const [formDate, setFormDate] = useState(todayStr())
  const [formStart, setFormStart] = useState('')
  const [formEnd, setFormEnd] = useState('')
  const [formLocation, setFormLocation] = useState('')
  const [formNote, setFormNote] = useState('')

  const flashToast = (msg: string) => {
    setToastMsg(msg)
    setTimeout(() => setToastMsg(null), 3000)
  }

  const fetchData = useCallback(() => {
    const baseDate = parseDateInput(selectedDate)
    if (tab === 'week') {
      const days: Date[] = []
      for (let i = 0; i < 7; i++) days.push(addDays(baseDate, i))
      Promise.all(days.map(d => getTimeline(fmtDate(d)).catch(() => null))).then(results => {
        const groups: DayGroup[] = []
        for (let i = 0; i < 7; i++) {
          const td = results[i]
          groups.push({
            date: fmtDate(days[i]),
            label: weekLabel(days[i]),
            events: td?.events ?? [],
          })
        }
        setWeekData(groups)
        setData(results[0])
      })
    } else {
      getTimeline(selectedDate).then(setData).catch(() => {})
    }
  }, [tab, selectedDate])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // Listen for refresh events from other components
  useEffect(() => {
    const handler = () => fetchData()
    window.addEventListener('dashboard-refresh', handler)
    return () => window.removeEventListener('dashboard-refresh', handler)
  }, [fetchData])

  const events = data?.events ?? []

  const resetForm = () => {
    setFormTitle('')
    setFormDate(selectedDate)
    setFormStart('')
    setFormEnd('')
    setFormLocation('')
    setFormNote('')
    setEditingEvent(null)
    setShowForm(false)
  }

  const shiftSelectedDate = (days: number) => {
    const shifted = fmtDate(addDays(parseDateInput(selectedDate), days))
    setSelectedDate(shifted)
    setTab('day')
  }

  const jumpToDate = (dateStr: string, nextTab: 'day' | 'week' = 'day') => {
    setSelectedDate(dateStr)
    setTab(nextTab)
    if (!editingEvent) setFormDate(dateStr)
  }

  const handleFormProposal = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!formTitle.trim() || !formDate || !formStart) return

    setLoading(true)
    try {
      const res = await postCalendarProposal({
        action: editingEvent ? 'update' : 'create',
        title: formTitle.trim(),
        date: formDate,
        start_time: formStart,
        end_time: formEnd || undefined,
        location: formLocation.trim() || undefined,
        note: formNote.trim() || undefined,
        event_id: editingEvent?.event_id,
        calendar_id: editingEvent?.calendar_id,
      })
      if (res.ok && res.proposal) {
        setProposalState({
          proposal: res.proposal,
          action_label: `${editingEvent ? '更新' : '创建'}日程：${formTitle.trim()}`,
          conflicts: res.conflicts,
        })
        resetForm()
        announceWebAction({
          ok: true,
          message: res.conflicts?.length ? '已创建提案（有冲突）' : '已创建提案，请确认写入日历',
          action: editingEvent ? 'calendar_update_proposal' : 'calendar_create_proposal',
          action_type: 'timeline_calendar_proposal',
          can_undo: false,
        })
        flashToast(res.conflicts?.length ? '已创建提案（有冲突）' : '已创建提案，请确认写入日历')
      } else {
        announceWebAction({
          ok: false,
          message: res.message || '创建提案失败',
          action: editingEvent ? 'calendar_update_proposal' : 'calendar_create_proposal',
          action_type: 'timeline_calendar_proposal',
          can_undo: false,
        })
        flashToast(res.message || '创建提案失败')
      }
    } catch (err: any) {
      flashToast('创建提案异常')
    } finally {
      setLoading(false)
    }
  }

  const handleUpdateProposal = async (ev: TimelineEvent) => {
    if (!ev.event_id) return
    setEditingEvent({ event_id: ev.event_id, calendar_id: ev.calendar_id || 'primary' })
    setFormTitle(ev.title || '')
    setFormDate(data?.date || todayStr())
    setFormStart(fmtTime(ev.start))
    setFormEnd(fmtTime(ev.end))
    setFormLocation(ev.location || '')
    setFormNote('')
    setShowForm(true)
    flashToast('已载入日程，修改后生成更新提案')
  }

  const handleDeleteProposal = async (ev: TimelineEvent) => {
    if (!ev.event_id) return
    setLoading(true)
    try {
      const res = await postCalendarProposal({
        action: 'delete',
        title: ev.title,
        event_id: ev.event_id,
        calendar_id: ev.calendar_id || 'primary',
      })
      if (res.ok && res.proposal) {
        setProposalState({
          proposal: res.proposal,
          action_label: `删除日程：${ev.title}`,
        })
        announceWebAction({
          ok: true,
          message: '已创建删除提案，请确认',
          action: 'calendar_delete_proposal',
          action_type: 'timeline_calendar_proposal',
          can_undo: false,
        })
        flashToast('已创建删除提案，请确认')
      } else {
        announceWebAction({
          ok: false,
          message: res.message || '创建删除提案失败',
          action: 'calendar_delete_proposal',
          action_type: 'timeline_calendar_proposal',
          can_undo: false,
        })
        flashToast(res.message || '创建删除提案失败')
      }
    } catch {
      flashToast('删除提案异常')
    } finally {
      setLoading(false)
    }
  }

  // ── Quick adjustment actions ─────────────────────────────────────
  const handleQuickAdjust = async (ev: TimelineEvent, adjustFn: (ev: TimelineEvent) => { date: string; start: string; end: string } | null) => {
    if (!ev.event_id) return
    const adjusted = adjustFn(ev)
    if (!adjusted) return
    setLoading(true)
    try {
      const res = await postCalendarProposal({
        action: 'update',
        title: ev.title,
        date: adjusted.date,
        start_time: adjusted.start,
        end_time: adjusted.end,
        event_id: ev.event_id,
        calendar_id: ev.calendar_id || 'primary',
      })
      if (res.ok && res.proposal) {
        setProposalState({
          proposal: res.proposal,
          action_label: `调整日程：${ev.title}`,
          conflicts: res.conflicts,
        })
        announceWebAction({
          ok: true,
          message: res.conflicts?.length ? '已创建调整提案（有冲突）' : '已创建调整提案',
          action: 'calendar_adjust_proposal',
          action_type: 'timeline_calendar_proposal',
          can_undo: false,
        })
        flashToast(res.conflicts?.length ? '已创建调整提案（有冲突）' : '已创建调整提案')
      } else {
        announceWebAction({
          ok: false,
          message: res.message || '调整提案失败',
          action: 'calendar_adjust_proposal',
          action_type: 'timeline_calendar_proposal',
          can_undo: false,
        })
        flashToast(res.message || '调整提案失败')
      }
    } catch {
      flashToast('调整提案异常')
    } finally {
      setLoading(false)
    }
  }

  const handleProposalDecision = async (decision: 'accept' | 'reject') => {
    if (!proposalState) return
    setDeciding(decision)
    try {
      const res = await postProposalDecision(proposalState.proposal, decision)
      flashToast(res.message)
      if (res.ok || decision === 'reject') {
        setProposalState(null)
      }
      if (res.ok) {
        announceWebAction({
          ok: true,
          message: res.message,
          action: `calendar_${decision}`,
          action_type: 'timeline_calendar_proposal_decision',
          can_undo: false,
        })
        refreshDashboard()
        fetchData()
      }
    } catch {
      flashToast('提案处理异常')
    } finally {
      setDeciding(null)
    }
  }

  // ── Quick action adjusters ───────────────────────────────────────
  function adjustEarlier30(ev: TimelineEvent): { date: string; start: string; end: string } | null {
    const s = ev.start
    const e = ev.end
    if (!s || !e) return null
    const dtS = new Date(s)
    const dtE = new Date(e)
    if (isNaN(dtS.getTime()) || isNaN(dtE.getTime())) return null
    dtS.setMinutes(dtS.getMinutes() - 30)
    dtE.setMinutes(dtE.getMinutes() - 30)
    return {
      date: fmtDate(dtS),
      start: fmtLocalTime(dtS),
      end: fmtLocalTime(dtE),
    }
  }

  function adjustLater30(ev: TimelineEvent): { date: string; start: string; end: string } | null {
    const s = ev.start
    const e = ev.end
    if (!s || !e) return null
    const dtS = new Date(s)
    const dtE = new Date(e)
    if (isNaN(dtS.getTime()) || isNaN(dtE.getTime())) return null
    dtS.setMinutes(dtS.getMinutes() + 30)
    dtE.setMinutes(dtE.getMinutes() + 30)
    return {
      date: fmtDate(dtS),
      start: fmtLocalTime(dtS),
      end: fmtLocalTime(dtE),
    }
  }

  function adjustExtend30(ev: TimelineEvent): { date: string; start: string; end: string } | null {
    const s = ev.start
    const e = ev.end
    if (!s) return null
    const dtS = new Date(s)
    if (isNaN(dtS.getTime())) return null
    let dtE: Date
    if (e && !isNaN(new Date(e).getTime())) {
      dtE = new Date(e)
    } else {
      dtE = new Date(dtS.getTime() + 3600000)
    }
    dtE.setMinutes(dtE.getMinutes() + 30)
    return {
      date: fmtDate(dtS),
      start: fmtLocalTime(dtS),
      end: fmtLocalTime(dtE),
    }
  }

  function adjustShorten30(ev: TimelineEvent): { date: string; start: string; end: string } | null {
    const s = ev.start
    const e = ev.end
    if (!s || !e) return null
    const dtS = new Date(s)
    const dtE = new Date(e)
    if (isNaN(dtS.getTime()) || isNaN(dtE.getTime())) return null
    if (dtE.getTime() - dtS.getTime() <= 30 * 60000) return null // too short
    dtE.setMinutes(dtE.getMinutes() - 30)
    return {
      date: fmtDate(dtS),
      start: fmtLocalTime(dtS),
      end: fmtLocalTime(dtE),
    }
  }

  function adjustTomorrow(ev: TimelineEvent): { date: string; start: string; end: string } | null {
    const s = ev.start
    const e = ev.end
    if (!s || !e) return null
    const dtS = new Date(s)
    const dtE = new Date(e)
    if (isNaN(dtS.getTime()) || isNaN(dtE.getTime())) return null
    const duration = dtE.getTime() - dtS.getTime()
    const tomorrow = addDays(new Date(), 1)
    const newS = new Date(tomorrow)
    newS.setHours(dtS.getHours(), dtS.getMinutes(), 0, 0)
    const newE = new Date(newS.getTime() + duration)
    return {
      date: fmtDate(newS),
      start: fmtLocalTime(newS),
      end: fmtLocalTime(newE),
    }
  }

  function hasValidTime(ev: TimelineEvent): boolean {
    if (!ev.start) return false
    return !isNaN(new Date(ev.start).getTime())
  }

  function renderProposalCard() {
    if (!proposalState) return null
    const hasConflicts = proposalState.conflicts && proposalState.conflicts.length > 0
    return (
      <div style={{
        background: 'linear-gradient(135deg, rgba(96,26,38,0.96), rgba(47,13,23,0.96))',
        border: hasConflicts ? '1px solid rgba(212,168,83,0.5)' : '1px solid rgba(255,255,255,0.1)',
        borderRadius: 22, padding: '14px 16px', marginBottom: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: 'var(--accent-pink)', marginBottom: 3 }}>日历提案</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
              {proposalState.action_label}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 3 }}>
              确认后才会写入 Google Calendar。
            </div>
            {/* Conflicts warning */}
            {hasConflicts && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent-gold)', marginBottom: 4 }}>
                  ⚠ 检测到 {proposalState.conflicts!.length} 个时间冲突
                </div>
                {proposalState.conflicts!.map((c, i) => (
                  <div key={i} style={{
                    fontSize: 11, color: 'var(--text-secondary)', marginLeft: 8, marginBottom: 2,
                  }}>
                    · {CONFLICT_SOURCE_LABELS[c.source] || c.source}：「{c.title}」
                    ({fmtTime(c.start)}–{fmtTime(c.end)})
                  </div>
                ))}
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 2 }}>
                  仍可接受，冲突仅供参考
                </div>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <button
              className="cmd-mini-btn primary"
              onClick={() => handleProposalDecision('accept')}
              disabled={deciding !== null}
            >
              {deciding === 'accept' ? '写入中' : '接受'}
            </button>
            <button
              className="cmd-mini-btn danger"
              onClick={() => handleProposalDecision('reject')}
              disabled={deciding !== null}
            >
              {deciding === 'reject' ? '拒绝中' : '拒绝'}
            </button>
            <button className="cmd-mini-btn" onClick={() => setProposalState(null)} disabled={deciding !== null}>
              关闭
            </button>
          </div>
        </div>
      </div>
    )
  }

  function renderEvents(evs: TimelineEvent[], showDateLabel = false, dateLabelStr = '') {
    if (evs.length === 0 && !showDateLabel) {
      return (
        <div style={{ color: 'var(--text-dim)', textAlign: 'center', padding: 20 }}>
          暂无事件
        </div>
      )
    }
    if (evs.length === 0 && showDateLabel) {
      return (
        <>
          <div style={{
            fontSize: 12, fontWeight: 600, color: 'var(--accent-pink)',
            padding: '6px 0 4px 0', marginTop: 8,
          }}>
            {dateLabelStr}
          </div>
          <div style={{ color: 'var(--text-dim)', fontSize: 12, padding: '4px 0 8px 22px' }}>
            暂无事件
          </div>
        </>
      )
    }
    return (
      <>
        {showDateLabel && (
          <div style={{
            fontSize: 12, fontWeight: 600, color: 'var(--accent-pink)',
            padding: '6px 0 4px 0', marginTop: 8,
          }}>
            {dateLabelStr}
          </div>
        )}
        {evs.map((ev: TimelineEvent, i: number) => (
          <div className="timeline-item" key={`${dateLabelStr}-${i}`}>
            <div className={`timeline-dot ${ev.source}`} />
            <div className="timeline-body">
              <div className="tl-title">{ev.title}</div>
              <div className="tl-time">
                {ev.start && fmtTime(ev.start)}{ev.end && ` — ${fmtTime(ev.end)}`}
                {ev.deadline && ` 截止 ${ev.deadline.slice(5, 10)}`}
                {ev.location && ` · ${ev.location}`}
              </div>
              <span className={`tl-source ${ev.source}`}>
                {SOURCE_LABELS[ev.source] || ev.source}
              </span>
              {/* Quick action buttons for Google Calendar events with valid times */}
              {ev.source === 'google_calendar' && ev.event_id && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                  <button className="tl-action-btn" onClick={() => handleUpdateProposal(ev)} disabled={loading} title="更新事件">更新</button>
                  <button className="tl-action-btn danger" onClick={() => handleDeleteProposal(ev)} disabled={loading} title="删除事件">删除</button>
                  {hasValidTime(ev) && (
                    <>
                      <button className="tl-action-btn" onClick={() => handleQuickAdjust(ev, adjustEarlier30)} disabled={loading} title="提前30分钟">提前30</button>
                      <button className="tl-action-btn" onClick={() => handleQuickAdjust(ev, adjustLater30)} disabled={loading} title="推迟30分钟">推迟30</button>
                      <button className="tl-action-btn" onClick={() => handleQuickAdjust(ev, adjustExtend30)} disabled={loading} title="延长30分钟">延长30</button>
                      <button className="tl-action-btn" onClick={() => handleQuickAdjust(ev, adjustShorten30)} disabled={loading} title="缩短30分钟">缩短30</button>
                      <button className="tl-action-btn" onClick={() => handleQuickAdjust(ev, adjustTomorrow)} disabled={loading} title="明天同时间">明天同时间</button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
      </>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600 }}>时间线</h2>
        <button
          className="timeline-tab"
          onClick={() => {
            if (showForm) resetForm()
            else {
              setFormDate(selectedDate)
              setShowForm(true)
            }
          }}
          style={{ background: showForm ? 'var(--accent-berry)' : 'transparent', color: showForm ? '#fff' : undefined }}
        >
          {showForm ? '关闭' : '+ 新建日程'}
        </button>
      </div>

      {/* Toast */}
      {toastMsg && (
        <div className={`cmd-toast ${toastMsg.includes('失败') || toastMsg.includes('异常') ? 'err' : 'ok'}`}>
          {toastMsg}
        </div>
      )}

      {/* Proposal card with conflict warning */}
      {renderProposalCard()}

      {/* Create event form */}
      {showForm && (
        <form onSubmit={handleFormProposal} style={{
          background: 'var(--bg-card)', borderRadius: 'var(--radius)',
          padding: 16, marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 10,
        }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>
            {editingEvent ? '更新日历事件' : '新建日历事件'}
          </div>
          <input required className="tl-form-input" placeholder="标题 *" value={formTitle} onChange={e => setFormTitle(e.target.value)} />
          <div style={{ display: 'flex', gap: 8 }}>
            <input required type="date" className="tl-form-input" style={{ flex: 1 }} value={formDate} onChange={e => setFormDate(e.target.value)} />
            <input required type="time" className="tl-form-input" style={{ width: 120 }} value={formStart} onChange={e => setFormStart(e.target.value)} />
            <input type="time" className="tl-form-input" style={{ width: 120 }} value={formEnd} onChange={e => setFormEnd(e.target.value)} placeholder="结束" />
          </div>
          <input className="tl-form-input" placeholder="地点" value={formLocation} onChange={e => setFormLocation(e.target.value)} />
          <input className="tl-form-input" placeholder="备注" value={formNote} onChange={e => setFormNote(e.target.value)} />
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" className="cmd-mini-btn primary" disabled={loading || !formTitle || !formDate || !formStart}>
              {loading ? '生成中...' : `${editingEvent ? '更新' : '创建'}提案`}
            </button>
            <button type="button" className="cmd-mini-btn" onClick={resetForm}>取消</button>
          </div>
        </form>
      )}

      {/* Tabs */}
      <div className="timeline-datebar">
        <button className="timeline-tab" onClick={() => shiftSelectedDate(-1)}>前一天</button>
        <input
          className="tl-form-input timeline-date-input"
          type="date"
          value={selectedDate}
          onChange={e => jumpToDate(e.target.value || todayStr())}
        />
        <button className="timeline-tab" onClick={() => shiftSelectedDate(1)}>后一天</button>
      </div>

      <div className="timeline-tabs">
        <button className={`timeline-tab ${tab === 'day' && selectedDate === todayStr() ? 'active' : ''}`} onClick={() => jumpToDate(todayStr())}>今天</button>
        <button className={`timeline-tab ${tab === 'day' && selectedDate === fmtDate(addDays(new Date(), 1)) ? 'active' : ''}`} onClick={() => jumpToDate(fmtDate(addDays(new Date(), 1)))}>明天</button>
        <button className={`timeline-tab ${tab === 'week' ? 'active' : ''}`} onClick={() => setTab('week')}>7天</button>
      </div>

      {tab === 'week' ? (
        <div className="timeline">
          {weekData.map((day, di) => (
            <div key={day.date}>
              {renderEvents(day.events, true, di === 0 ? `今天 ${day.date}` : `${day.label} ${day.date}`)}
            </div>
          ))}
          {weekData.every(d => d.events.length === 0) && (
            <div style={{ color: 'var(--text-dim)', textAlign: 'center', padding: 40 }}>
              本周暂无事件
            </div>
          )}
        </div>
      ) : (
        <>
          {data && (
            <div style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 12 }}>
              {selectedDate} · {events.length} 个事件
            </div>
          )}
          <div className="timeline">
            {renderEvents(events)}
          </div>
        </>
      )}
    </div>
  )
}

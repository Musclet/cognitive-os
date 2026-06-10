import React, { useEffect, useState } from 'react'
import { getDashboard, DashboardData, postReviewAction } from '../api'
import { Ring } from '../components/Ring'
import { announceWebAction, refreshDashboard } from '../events'

interface Props { onAction?: (text: string, action?: string) => void }

export function Review({ onAction: _onAction }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [moodScore, setMoodScore] = useState('')
  const [energyScore, setEnergyScore] = useState('')
  const [pressureScore, setPressureScore] = useState('')
  const [bodyState, setBodyState] = useState('')
  const [completed, setCompleted] = useState('')
  const [deviation, setDeviation] = useState('')
  const [tomorrow, setTomorrow] = useState('')
  const [note, setNote] = useState('')
  const [reviewStatus, setReviewStatus] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const loadDashboard = () => {
    getDashboard().then(setData).catch(() => {})
  }

  useEffect(() => {
    loadDashboard()
    const handler = () => loadDashboard()
    window.addEventListener('dashboard-refresh', handler)
    return () => window.removeEventListener('dashboard-refresh', handler)
  }, [])

  const handleReviewSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const hasContent = [
      moodScore, energyScore, pressureScore,
      bodyState, completed, deviation, tomorrow, note,
    ].some(v => String(v).trim())
    if (!hasContent || submitting) return

    setSubmitting(true)
    setReviewStatus(null)
    try {
      const res = await postReviewAction({
        mood_score: moodScore ? Number(moodScore) : undefined,
        energy_score: energyScore ? Number(energyScore) : undefined,
        pressure_score: pressureScore ? Number(pressureScore) : undefined,
        body_state: bodyState || undefined,
        completed: completed || undefined,
        deviation: deviation || undefined,
        tomorrow: tomorrow || undefined,
        note: note || undefined,
      })
      if (res.dashboard) setData(res.dashboard)
      setReviewStatus({ type: 'ok', text: res.message || '已记录今日复盘' })
      setBodyState('')
      setCompleted('')
      setDeviation('')
      setTomorrow('')
      setNote('')
      announceWebAction({
        ok: res.ok,
        message: res.message || '已记录今日复盘',
        action: 'daily_review',
        action_type: 'review_record',
        can_undo: false,
      })
      refreshDashboard()
    } catch (err: any) {
      setReviewStatus({ type: 'err', text: err.detail || '复盘记录失败' })
    } finally {
      setSubmitting(false)
      window.setTimeout(() => setReviewStatus(null), 3600)
    }
  }

  if (!data) return <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-dim)' }}>加载中...</div>

  const pressure = data.deadline_pressure?.score ?? 0
  const workload = data.workload_density?.score ?? 0
  const capacityPressure = data.workload_density?.capacity_pressure ?? 0
  const fitnessPct = data.fitness?.completion_pct ?? 0
  const hwCount = data.homework_count ?? 0

  return (
    <div>
      <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>复盘</h2>

      <form onSubmit={handleReviewSubmit} className="review-form card">
        <div className="review-form-head">
          <div>
            <div className="review-kicker">每日认知输入</div>
            <h3>今日复盘表</h3>
          </div>
          <button
            type="submit"
            className="review-submit"
            disabled={submitting || ![moodScore, energyScore, pressureScore, bodyState, completed, deviation, tomorrow, note].some(v => String(v).trim())}
          >
            {submitting ? '记录中' : '写入复盘'}
          </button>
        </div>

        <div className="review-score-row">
          <label>
            <span>心情</span>
            <input type="number" min="1" max="10" value={moodScore} onChange={e => setMoodScore(e.target.value)} placeholder="1-10" />
          </label>
          <label>
            <span>精力</span>
            <input type="number" min="1" max="10" value={energyScore} onChange={e => setEnergyScore(e.target.value)} placeholder="1-10" />
          </label>
          <label>
            <span>压力</span>
            <input type="number" min="1" max="10" value={pressureScore} onChange={e => setPressureScore(e.target.value)} placeholder="1-10" />
          </label>
        </div>

        <div className="review-field-grid">
          <label>
            <span>身体状态</span>
            <input value={bodyState} onChange={e => setBodyState(e.target.value)} placeholder="困 / 累 / 清醒 / 肌肉酸..." />
          </label>
          <label>
            <span>今天完成</span>
            <textarea value={completed} onChange={e => setCompleted(e.target.value)} placeholder="完成了什么，多少时间，质量如何" />
          </label>
          <label>
            <span>偏离原因</span>
            <textarea value={deviation} onChange={e => setDeviation(e.target.value)} placeholder="为什么没按计划，卡在哪里" />
          </label>
          <label>
            <span>明日调整</span>
            <textarea value={tomorrow} onChange={e => setTomorrow(e.target.value)} placeholder="明天要避开的坑 / 要保留的主线" />
          </label>
        </div>

        <div className="review-note-row">
          <input value={note} onChange={e => setNote(e.target.value)} placeholder="其他备注，可留空" />
          <div className="review-chip-row">
            <button type="button" onClick={() => setDeviation('临时安排挤占时间')}>临时安排</button>
            <button type="button" onClick={() => setDeviation('状态差，执行阻力高')}>状态差</button>
            <button type="button" onClick={() => setTomorrow('先画画，再处理系统优化')}>先画画</button>
          </div>
        </div>

        {reviewStatus && <div className={`review-status ${reviewStatus.type}`}>{reviewStatus.text}</div>}
      </form>

      <div className="cards-grid">
        <div className="card">
          <h3>📊 完成率</h3>
          <div style={{ textAlign: 'center', marginBottom: 12 }}>
            <Ring pct={fitnessPct} size={72} color="pink" />
            <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-dim)' }}>
              训练 {fitnessPct}%
            </div>
          </div>
          <div className="stat-row">
            <span className="label">待办作业</span>
            <span className="value">{hwCount} 项</span>
          </div>
        </div>

        <div className="card">
          <h3>⚠️ 压力与负荷</h3>
          <div className="stat-row">
            <span className="label">压力分数</span>
            <span className="value">{(pressure * 100).toFixed(0)}%</span>
          </div>
          <div className="stat-row">
            <span className="label">负荷分数</span>
            <span className="value">{(workload * 100).toFixed(0)}%</span>
          </div>
          <div className="stat-row">
            <span className="label">容量压力</span>
            <span className="value">{(capacityPressure * 100).toFixed(0)}%</span>
          </div>
        </div>

        <div className="card">
          <h3>📈 趋势</h3>
          <div className="stat-row">
            <span className="label">压力趋势</span>
            <span className="value">{data.deadline_pressure?.trend || 'stable'}</span>
          </div>
          <div className="stat-row">
            <span className="label">活跃课程</span>
            <span className="value">{data.active_context?.active_course_count || 0}</span>
          </div>
          {data.deadline_pressure?.overdue_count > 0 && (
            <div className="stat-row">
              <span className="label">逾期作业</span>
              <span className="value" style={{ color: 'var(--danger)' }}>
                {data.deadline_pressure.overdue_count}
              </span>
            </div>
          )}
        </div>

        <div className="card">
          <h3>🧠 主观状态</h3>
          <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            数据通过每日复盘收集
            <br />
            <span style={{ fontSize: 11 }}>请查看 Obsidian 日常记录</span>
          </div>
        </div>
      </div>
    </div>
  )
}

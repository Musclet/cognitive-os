import React, { useEffect, useState, useCallback, useRef } from 'react'
import {
  getWorkoutSession, selectWorkoutDay,
  updateSet, addSet, deleteSet, duplicateSet,
  moveExercise, updateExercise, addExercise, deleteExercise,
  WorkoutSession,
} from '../api'
import { Ring } from '../components/Ring'

type WorkoutKind = 'upper' | 'lower' | 'rest'
type SwitchDirection = 'next' | 'prev'
type TransitionPhase = 'idle' | 'exit' | 'enter'

function workoutKind(day?: string): WorkoutKind {
  const normalized = (day || '').toLowerCase()
  if (normalized.includes('lower')) return 'lower'
  if (normalized.includes('rest')) return 'rest'
  return 'upper'
}

function workoutLabel(day: string): string {
  const kind = workoutKind(day)
  if (kind === 'lower') return '下肢力量'
  if (kind === 'rest') return '恢复与重置'
  return '上肢力量'
}

function wait(ms: number) {
  return new Promise(resolve => window.setTimeout(resolve, ms))
}

function WorkoutMotionFigure({ kind, performing }: { kind: WorkoutKind; performing: boolean }) {
  return (
    <div className={`fit-motion-figure ${kind} ${performing ? 'is-performing' : ''}`} aria-hidden="true">
      <svg viewBox="0 0 240 210" role="img">
        <g className="fit-motion-floor">
          <path d="M28 186H212" />
          <path d="M72 194H168" />
        </g>
        <g className="fit-athlete">
          <circle className="fit-athlete-head" cx="120" cy="58" r="16" />
          <path className="fit-athlete-torso" d="M120 77V132" />
          <g className="fit-athlete-arms">
            <path d="M120 88L88 109L70 87" />
            <path d="M120 88L152 109L170 87" />
          </g>
          <g className="fit-athlete-legs">
            <path d="M120 132L91 178" />
            <path d="M120 132L149 178" />
          </g>
        </g>
        <g className="fit-barbell">
          <path d="M58 87H182" />
          <path d="M52 73V101M58 70V104M182 70V104M188 73V101" />
        </g>
        <g className="fit-rest-pulse">
          <circle cx="120" cy="108" r="47" />
          <circle cx="120" cy="108" r="66" />
        </g>
      </svg>
    </div>
  )
}

export function Fitness() {
  const [session, setSession] = useState<WorkoutSession | null>(null)
  const [saveStatus, setSaveStatus] = useState('')
  const [switchingDay, setSwitchingDay] = useState<string | null>(null)
  const [switchDirection, setSwitchDirection] = useState<SwitchDirection>('next')
  const [transitionPhase, setTransitionPhase] = useState<TransitionPhase>('idle')
  const [sessionMotionKey, setSessionMotionKey] = useState(0)
  const transitionTimerRef = useRef<ReturnType<typeof setTimeout>>()

  // Exercise editing state
  const [editEx, setEditEx] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [editNotes, setEditNotes] = useState('')

  // Add exercise form state
  const [showAddForm, setShowAddForm] = useState(false)
  const [addName, setAddName] = useState('')
  const [addSets, setAddSets] = useState(3)
  const [addTargetReps, setAddTargetReps] = useState('8-12')
  const [addNotes, setAddNotes] = useState('')

  // Rest timer
  const [timerRemaining, setTimerRemaining] = useState<number | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval>>()
  useEffect(() => () => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (transitionTimerRef.current) clearTimeout(transitionTimerRef.current)
  }, [])

  const load = useCallback(() => {
    getWorkoutSession().then(setSession).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  if (!session) {
    return <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-dim)' }}>加载中...</div>
  }

  const s = session.session
  const pct = s && s.total_sets > 0 ? Math.round((s.completed_sets / s.total_sets) * 100) : 0
  const visibleDay = switchingDay || s?.training_day || session.recommended_day || session.planned_day
  const visibleKind = workoutKind(visibleDay)
  const availableDays = session.available_days || []

  const handleSelectDay = async (day: string) => {
    if (switchingDay || day === s?.training_day) return
    const currentIndex = Math.max(0, availableDays.indexOf(s?.training_day))
    const nextIndex = Math.max(0, availableDays.indexOf(day))
    const direction: SwitchDirection = nextIndex >= currentIndex ? 'next' : 'prev'
    setSwitchDirection(direction)
    setSwitchingDay(day)
    setTransitionPhase('exit')
    setSaveStatus(`正在加载 ${day}...`)
    const startedAt = Date.now()

    try {
      let result: WorkoutSession
      try {
        result = await selectWorkoutDay(session.date, day, false)
      } catch (err: any) {
        if (err.status !== 409 || !window.confirm('今天已有训练记录。覆盖并切换？')) throw err
        result = await selectWorkoutDay(session.date, day, true)
      }
      await wait(Math.max(0, 680 - (Date.now() - startedAt)))
      setSession(result)
      setSessionMotionKey(value => value + 1)
      setTransitionPhase('enter')
      setSaveStatus('已就绪')
      transitionTimerRef.current = setTimeout(() => {
        setTransitionPhase('idle')
        setSwitchingDay(null)
      }, 760)
    } catch (err: any) {
      setTransitionPhase('idle')
      setSwitchingDay(null)
      setSaveStatus(err.status === 409 ? '已取消切换' : '错误: ' + (err.detail || err.message))
    }
  }

  const handleUpdate = async (exIdx: number, setNum: number, field: string, value: any) => {
    setSaveStatus('保存中...')
    try {
      const result = await updateSet(session.date, exIdx + 1, setNum, field, value)
      setSession(result)
      setSaveStatus('已保存')
      if (field === 'checked' && value === true) startTimer(90)
    } catch { setSaveStatus('保存失败') }
  }

  const handleAdd = async (exIdx: number) => {
    setSaveStatus('添加中...')
    try {
      const result = await addSet(session.date, exIdx + 1)
      setSession(result)
      setSaveStatus('已添加')
    } catch { setSaveStatus('添加失败') }
  }

  const handleDelete = async (exIdx: number, setNum: number) => {
    if (!window.confirm(`删除第 ${setNum} 组？`)) return
    setSaveStatus('删除中...')
    try {
      const result = await deleteSet(session.date, exIdx + 1, setNum)
      setSession(result)
      setSaveStatus('已删除')
    } catch { setSaveStatus('删除失败') }
  }

  // ── Rest timer ───────────────────────────────────────────────
  const startTimer = (seconds: number) => {
    if (timerRef.current) clearInterval(timerRef.current)
    setTimerRemaining(seconds)
    timerRef.current = setInterval(() => {
      setTimerRemaining(prev => {
        if (prev === null || prev <= 1) {
          if (timerRef.current) clearInterval(timerRef.current)
          timerRef.current = undefined
          return 0
        }
        return prev - 1
      })
    }, 1000)
  }
  const stopTimer = () => {
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = undefined
    setTimerRemaining(null)
  }
  const formatTimer = (s: number) => {
    const m = Math.floor(s / 60)
    return `${m}:${(s % 60).toString().padStart(2, '0')}`
  }

  // ── Exercise actions ─────────────────────────────────────────
  const handleDuplicate = async (exIdx: number) => {
    setSaveStatus('复制中...')
    try { const r = await duplicateSet(session.date, exIdx + 1); setSession(r); setSaveStatus('已复制') }
    catch { setSaveStatus('复制失败') }
  }
  const handleMoveEx = async (exIdx: number, dir: 'up' | 'down') => {
    setSaveStatus('移动中...')
    try { const r = await moveExercise(session.date, exIdx + 1, dir); setSession(r); setSaveStatus('已移动') }
    catch { setSaveStatus('移动失败') }
  }
  const handleEditStart = (exIdx: number, name: string, notes: string) => {
    setEditEx(exIdx); setEditName(name); setEditNotes(notes)
  }
  const handleEditSave = async (exIdx: number) => {
    if (!editName.trim()) {
      setSaveStatus('动作名称不能为空')
      return
    }
    setSaveStatus('保存中...')
    try {
      const r = await updateExercise(session.date, exIdx + 1, editName, editNotes)
      setSession(r); setSaveStatus('已保存'); setEditEx(null)
    } catch { setSaveStatus('保存失败') }
  }
  const handleEditCancel = () => setEditEx(null)
  const handleDeleteEx = async (exIdx: number, name: string) => {
    if (!window.confirm(`删除「${name}」及其所有组？`)) return
    setSaveStatus('删除中...')
    try { const r = await deleteExercise(session.date, exIdx + 1); setSession(r); setSaveStatus('已删除') }
    catch { setSaveStatus('删除失败') }
  }
  const handleAddEx = async () => {
    if (!addName.trim()) return
    setSaveStatus('添加中...')
    try {
      const r = await addExercise(session.date, addName.trim(), addTargetReps, addNotes, addSets)
      setSession(r); setSaveStatus('已添加'); setShowAddForm(false); setAddName(''); setAddSets(3); setAddTargetReps('8-12'); setAddNotes('')
    } catch { setSaveStatus('添加失败') }
  }

  return (
    <div className="fitness-page">
      <div className="fit-header">
        <div>
          <div className="section-eyebrow">FITNESS / 训练</div>
          <h1>今天，身体负责回答</h1>
          <div className="fit-header-date">{session.date} · {session.weekday}</div>
        </div>
        <div className="fit-progress">
          <Ring pct={pct} size={74} strokeWidth={4} />
          <div>
            <strong>{pct}%</strong>
            <span>今日完成度</span>
          </div>
        </div>
      </div>

      <div className="fit-template-head">
        <div>
          <span>训练模板</span>
          <strong>选择今天的身体主题</strong>
        </div>
        <small>悬停预览 · 点击切换</small>
      </div>

      <div className="fit-day-select" role="list" aria-label="训练模板">
        {availableDays.map((day: string, index: number) => (
          <button
            key={day}
            className={`fit-day-btn ${s?.training_day === day ? 'active' : ''} ${switchingDay === day ? 'loading' : ''}`}
            onClick={() => handleSelectDay(day)}
            disabled={Boolean(switchingDay)}
            aria-pressed={s?.training_day === day}
            data-kind={workoutKind(day)}
          >
            <span className="fit-day-index">{String(index + 1).padStart(2, '0')}</span>
            <span className="fit-day-copy">
              <strong>{day}</strong>
              <small>{workoutLabel(day)}</small>
            </span>
            <span className="fit-day-arrow" aria-hidden="true">↗</span>
          </button>
        ))}
      </div>

      <section
        className={`fit-motion-stage ${transitionPhase !== 'idle' ? 'is-performing' : ''}`}
        data-kind={visibleKind}
      >
        <div className="fit-motion-copy">
          <span>{switchingDay ? 'LOADING PROGRAM' : 'CURRENT PROGRAM'}</span>
          <strong>{visibleDay || '选择训练模板'}</strong>
          <p>
            {switchingDay
              ? `${workoutLabel(switchingDay)}正在展开`
              : s?.focus || workoutLabel(visibleDay || '')}
          </p>
        </div>
        <WorkoutMotionFigure kind={visibleKind} performing={transitionPhase !== 'idle'} />
        <div className="fit-motion-status">
          <span>{transitionPhase === 'exit' ? '蓄力' : transitionPhase === 'enter' ? '动作已载入' : 'READY'}</span>
          <i />
        </div>
      </section>

      <div
        className="fit-session-viewport"
        data-phase={transitionPhase}
        data-direction={switchDirection}
      >
        <div
          className="fit-session-content"
          key={`${s?.training_day || 'empty'}-${sessionMotionKey}`}
          data-phase={transitionPhase}
          data-direction={switchDirection}
        >
          {s?.focus && (
            <div className="fit-session-summary data-birth">
              <span>{s.training_day}</span>
              <strong>{s.focus}</strong>
              <small>{s.completed_sets}/{s.total_sets} 组完成</small>
            </div>
          )}

          {/* ── Rest Timer ────────────────────────────────────── */}
          <div className="fit-timer data-birth">
            <div className="fit-timer-label">
              <span>REST TIMER</span>
              <strong>组间恢复</strong>
            </div>
            <div className="fit-timer-presets">
              {[60, 90, 120].map(sec => (
                <button key={sec} className="fit-timer-btn"
                  onClick={() => startTimer(sec)} disabled={timerRemaining !== null && timerRemaining > 0}>
                  {sec}s
                </button>
              ))}
            </div>
            <div className="fit-timer-display">
              {timerRemaining !== null ? formatTimer(timerRemaining) : '1:30'}
            </div>
            <div className="fit-timer-actions">
              {timerRemaining !== null && timerRemaining > 0 ? (
                <button className="fit-timer-btn primary" onClick={stopTimer}>停止</button>
              ) : (
                <button className="fit-timer-btn primary" onClick={() => startTimer(90)}>开始</button>
              )}
              {timerRemaining !== null && <button className="fit-timer-btn" onClick={stopTimer}>重置</button>}
            </div>
          </div>

          {/* ── Exercises ─────────────────────────────────────── */}
          <div className="fit-exercise-list">
          {s?.exercises?.length ? s.exercises.map((ex: any, ei: number) => (
        <div
          className="card exercise-card data-birth"
          key={`${ex.index}-${ex.name}`}
          style={{ animationDelay: `${Math.min(ei, 8) * 75}ms` }}
        >
          <div className="exercise-header">
            {editEx === ei ? (
              <div className="exercise-edit-inline">
                <input className="fit-input" value={editName} onChange={e => setEditName(e.target.value)}
                  placeholder="动作名称" />
                <input className="fit-input" value={editNotes} onChange={e => setEditNotes(e.target.value)}
                  placeholder="备注（可选）" />
                <div className="exercise-edit-actions">
                  <button disabled={!editName.trim()} onClick={() => handleEditSave(ei)}>保存</button>
                  <button onClick={handleEditCancel}>取消</button>
                </div>
              </div>
            ) : (
              <>
                <span className="exercise-name">{ex.index}. {ex.name}</span>
                <div className="exercise-header-actions">
                  <button className="ex-icon-btn" title="上移" onClick={() => handleMoveEx(ei, 'up')}>▲</button>
                  <button className="ex-icon-btn" title="下移" onClick={() => handleMoveEx(ei, 'down')}>▼</button>
                  <button className="ex-icon-btn" title="编辑" onClick={() => handleEditStart(ei, ex.name, ex.notes || '')}>✎</button>
                  <button className="ex-icon-btn ex-icon-btn--danger" title="删除" onClick={() => handleDeleteEx(ei, ex.name)}>✕</button>
                </div>
              </>
            )}
          </div>
          {editEx !== ei && ex.notes && <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 6 }}>{ex.notes}</div>}
          {ex.sets?.map((set: any) => (
            <div className="set-row" key={set.set_number}>
              <span className="set-num">{set.set_number}</span>
              <input
                type="checkbox"
                checked={set.checked}
                onChange={e => handleUpdate(ei, set.set_number, 'checked', e.target.checked)}
              />
              <input
                type="text" value={set.weight} placeholder="重量"
                onChange={e => handleUpdate(ei, set.set_number, 'weight', e.target.value)}
                inputMode="decimal"
              />
              <input
                type="text" value={set.reps} placeholder="次数"
                onChange={e => handleUpdate(ei, set.set_number, 'reps', e.target.value)}
                inputMode="numeric"
              />
              <span className="set-target">/{set.target_reps}</span>
              <input
                type="text" value={set.rir} placeholder="RIR"
                onChange={e => handleUpdate(ei, set.set_number, 'rir', e.target.value)}
                inputMode="numeric"
                style={{ width: 40 }}
              />
              <button className="set-del" onClick={() => handleDelete(ei, set.set_number)}>✕</button>
            </div>
          ))}
          <div className="exercise-actions">
            <button onClick={() => handleAdd(ei)}>+ 组</button>
            <button onClick={() => handleDuplicate(ei)}>复制末组</button>
          </div>
        </div>
      )) : (
        <div className="fit-empty-program data-birth">
          <span>{visibleKind === 'rest' ? 'REST DAY' : 'NO EXERCISES'}</span>
          <strong>{visibleKind === 'rest' ? '今天的训练是恢复' : '选择训练日开始'}</strong>
          <p>{visibleKind === 'rest' ? '散步、伸展，给下一次训练留下空间。' : '动作会在角色完成发力后从下方带出。'}</p>
        </div>
      )}
          </div>

          {/* ── Add exercise form ─────────────────────────────── */}
          {s?.exercises && s.exercises.length > 0 && (
        <div className="card fit-add-card data-birth">
          {!showAddForm ? (
            <button className="fit-add-ex-btn" onClick={() => setShowAddForm(true)}>+ 添加自定义动作</button>
          ) : (
            <div className="fit-add-ex-form">
              <div className="fit-add-ex-row">
                <input className="fit-input" value={addName} onChange={e => setAddName(e.target.value)}
                  placeholder="动作名称 *" style={{ flex: 1 }} />
                <input className="fit-input" type="number" value={addSets} onChange={e => setAddSets(Math.max(1, Number(e.target.value)))}
                  placeholder="组数" style={{ width: 60 }} min={1} max={20} />
                <input className="fit-input" value={addTargetReps} onChange={e => setAddTargetReps(e.target.value)}
                  placeholder="目标次数" style={{ width: 80 }} />
              </div>
              <input className="fit-input" value={addNotes} onChange={e => setAddNotes(e.target.value)}
                placeholder="备注（可选）" style={{ width: '100%' }} />
              <div className="fit-add-ex-actions">
                <button disabled={!addName.trim()} onClick={handleAddEx}>添加</button>
                <button onClick={() => { setShowAddForm(false); setAddName('') }}>取消</button>
              </div>
            </div>
          )}
        </div>
          )}
        </div>
      </div>

      <div className="fit-save-status" role="status" aria-live="polite">
        {saveStatus}
      </div>
    </div>
  )
}

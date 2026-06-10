import React, { useEffect, useState, useCallback, useRef } from 'react'
import {
  getWorkoutSession, selectWorkoutDay,
  updateSet, addSet, deleteSet, duplicateSet,
  moveExercise, updateExercise, addExercise, deleteExercise,
  WorkoutSession,
} from '../api'
import { Ring } from '../components/Ring'

export function Fitness() {
  const [session, setSession] = useState<WorkoutSession | null>(null)
  const [saveStatus, setSaveStatus] = useState('')

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
  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current) }, [])

  const load = useCallback(() => {
    getWorkoutSession().then(setSession).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  if (!session) {
    return <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-dim)' }}>加载中...</div>
  }

  const s = session.session
  const pct = s && s.total_sets > 0 ? Math.round((s.completed_sets / s.total_sets) * 100) : 0

  const handleSelectDay = async (day: string) => {
    setSaveStatus('选择中...')
    try {
      const result = await selectWorkoutDay(session.date, day, false)
      setSession(result)
      setSaveStatus('已就绪')
    } catch (err: any) {
      if (err.status === 409) {
        if (window.confirm('今天已有训练记录。覆盖并切换？')) {
          const result = await selectWorkoutDay(session.date, day, true)
          setSession(result)
          setSaveStatus('已切换')
        }
      } else {
        setSaveStatus('错误: ' + (err.detail || err.message))
      }
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
    <div>
      <div className="fit-header">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600 }}>健身</h2>
          <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>{session.date} {session.weekday}</div>
        </div>
        <Ring pct={pct} size={56} />
      </div>

      <div className="fit-day-select">
        {(session.available_days || []).map((day: string) => (
          <button
            key={day}
            className={`fit-day-btn ${s?.training_day === day ? 'active' : ''}`}
            onClick={() => handleSelectDay(day)}
          >
            {day}
          </button>
        ))}
      </div>

      {s?.focus && (
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
          {s.training_day} · {s.focus} · {s.completed_sets}/{s.total_sets} 组
        </div>
      )}

      {/* ── Rest Timer ────────────────────────────────────────── */}
      <div className="fit-timer">
        <div className="fit-timer-presets">
          {[60, 90, 120].map(sec => (
            <button key={sec} className={`fit-timer-btn${timerRemaining !== null && timerRemaining > 0 ? '' : ''}`}
              onClick={() => startTimer(sec)} disabled={timerRemaining !== null && timerRemaining > 0}>
              {sec}s
            </button>
          ))}
        </div>
        <div className="fit-timer-display">
          {timerRemaining !== null ? formatTimer(timerRemaining) : '—'}
        </div>
        <div className="fit-timer-actions">
          {timerRemaining !== null && timerRemaining > 0 ? (
            <button className="fit-timer-btn" onClick={stopTimer}>停止</button>
          ) : (
            <button className="fit-timer-btn" onClick={() => startTimer(90)} disabled={false}>开始</button>
          )}
          {timerRemaining !== null && <button className="fit-timer-btn" onClick={stopTimer}>重置</button>}
        </div>
      </div>

      {/* ── Exercises ─────────────────────────────────────────── */}
      {s?.exercises?.map((ex: any, ei: number) => (
        <div className="card exercise-card" key={ei}>
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
      )) || (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 24 }}>
          选择训练日开始
        </div>
      )}

      {/* ── Add exercise form ─────────────────────────────────── */}
      {s?.exercises && s.exercises.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
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

      <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--text-dim)', marginTop: 8 }}>
        {saveStatus}
      </div>
    </div>
  )
}

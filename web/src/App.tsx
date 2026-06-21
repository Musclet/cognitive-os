import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import { checkAuth, login, logout, postAction, postUndo, postProposalDecision } from './api'
import { NavDock } from './components/NavDock'
import { BottomNav } from './components/BottomNav'
import { Overview } from './pages/Overview'
import { TimelinePage } from './pages/TimelinePage'
import { Tasks } from './pages/Tasks'
import { Fitness } from './pages/Fitness'
import { Finance } from './pages/Finance'
import { Review } from './pages/Review'
import { SystemPage } from './pages/System'
import { refreshDashboard } from './events'

interface QuickShortcut {
  label: string
  text: string
  action?: string
  routes?: string[]
}

const QUICK_SHORTCUTS: QuickShortcut[] = [
  { label: '画画30', text: '完成了画画30分钟', action: 'complete_art_30', routes: ['overview', 'review'] },
  { label: '画画60', text: '完成了画画60分钟', action: 'complete_art_60', routes: ['overview', 'review'] },
  { label: '补水500', text: '补水500', action: 'hydration_500', routes: ['overview', 'fitness'] },
  { label: '状态差', text: '状态差', action: 'bad_state', routes: ['overview', 'review'] },
  { label: '同步刷新', text: '同步刷新数据', action: 'sync_refresh', routes: ['overview', 'system'] },
  { label: '完成作业', text: '完成了作业', action: 'complete_homework', routes: ['tasks', 'overview'] },
  { label: '作业稍后', text: '作业稍后30分钟', action: 'delay_homework_30', routes: ['tasks', 'overview'] },
  { label: '同步作业', text: '同步作业', action: 'check_homework', routes: ['tasks', 'system'] },
  { label: '明天吃饭', text: '明天中午十二点吃饭', routes: ['timeline', 'overview'] },
  { label: '今晚画画', text: '今晚八点画画两小时', routes: ['timeline', 'overview'] },
  { label: '花20', text: '花了20买饭', action: 'log_finance_spend', routes: ['finance'] },
  { label: '到账100', text: '生活费到账100', routes: ['finance'] },
  { label: '今天请假', text: '请假今天', action: 'school_leave_today', routes: ['timeline', 'system'] },
]

const ROUTE_MOTION: Record<string, { label: string; tone: string }> = {
  overview: { label: 'TODAY', tone: 'red' },
  tasks: { label: 'TASKS', tone: 'amber' },
  timeline: { label: 'TIME', tone: 'mint' },
  review: { label: 'REVIEW', tone: 'purple' },
  fitness: { label: 'FITNESS', tone: 'mint' },
  finance: { label: 'FINANCE', tone: 'amber' },
  system: { label: 'SYSTEM', tone: 'ink' },
}

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [pin, setPin] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // Auto-focus and re-focus on click anywhere
  useEffect(() => {
    inputRef.current?.focus()
    const handler = () => inputRef.current?.focus()
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!pin || loading) return
    setError('')
    setLoading(true)
    try {
      await login(pin)
      onLogin()
    } catch (err: any) {
      setError(err.detail || '验证失败')
      setPin('')
      inputRef.current?.focus()
    } finally {
      setLoading(false)
    }
  }

  const handlePinChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value.replace(/\D/g, '').slice(0, 6)
    setPin(val)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setPin('')
      setError('')
    }
  }

  const pinDots = Array.from({ length: 6 }, (_, i) => i < pin.length)

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-icon">🧠</div>
        <h1>Cognitive OS</h1>
        <p className="login-subtitle">输入 6 位 PIN 解锁控制台</p>
        <form onSubmit={handleSubmit}>
          <div className="pin-dots-row">
            {pinDots.map((filled, i) => (
              <div key={i} className={`pin-dot ${filled ? 'filled' : ''} ${error ? 'error' : ''}`} />
            ))}
          </div>
          <input
            ref={inputRef}
            type="password"
            inputMode="numeric"
            maxLength={6}
            className="pin-input-hidden"
            placeholder="输入 PIN..."
            value={pin}
            onChange={handlePinChange}
            onKeyDown={handleKeyDown}
            autoFocus
            autoComplete="off"
          />
          {error && <p className="login-error">{error}</p>}
          <button type="submit" className="login-btn" disabled={loading || pin.length < 4}>
            {loading ? (
              <span className="login-btn-loading">
                <span className="loading-dot" />
                验证中...
              </span>
            ) : (
              '解锁'
            )}
          </button>
        </form>
        <p className="login-hint">Esc 清除 · Enter 确认</p>
      </div>
    </div>
  )
}

function MainShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const mainRef = useRef<HTMLElement>(null)
  const pointerAuraRef = useRef<HTMLDivElement>(null)
  const scrollProgressRef = useRef<HTMLDivElement>(null)
  const [cmdText, setCmdText] = useState('')
  const [cmdStatus, setCmdStatus] = useState<{ msg: string; ok: boolean } | null>(null)
  const [lastAction, setLastAction] = useState<{ id: string; type: string } | null>(null)
  const [lastProposal, setLastProposal] = useState<any | null>(null)
  const [undoing, setUndoing] = useState(false)
  const [cmdSubmitting, setCmdSubmitting] = useState(false)
  const [decidingProposal, setDecidingProposal] = useState<'accept' | 'reject' | null>(null)
  const [composerOpen, setComposerOpen] = useState(false)
  const cmdTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cmdSubmittingRef = useRef(false)
  const { showPrompt: showInstall, install: handleInstall, dismiss: dismissInstall } = useInstallPrompt()

  const flashStatus = (msg: string, ok: boolean) => {
    setCmdStatus({ msg, ok })
    if (cmdTimer.current) clearTimeout(cmdTimer.current)
    cmdTimer.current = setTimeout(() => setCmdStatus(null), 3500)
  }

  const executeAction = async (text: string, action?: string, payload?: Record<string, any>) => {
    if (cmdSubmittingRef.current) return
    cmdSubmittingRef.current = true
    setCmdSubmitting(true)
    setCmdStatus(null)
    try {
      const res = await postAction(text, action, payload)
      flashStatus(res.message, res.ok)
      if (res.proposal) {
        setLastProposal(res.proposal)
      }
      if (res.action_id) {
        setLastAction({ id: res.action_id, type: res.action_type || res.command_type })
      }
      if (!res.needs_followup && res.ok) {
        refreshDashboard()
      }
    } catch (err: any) {
      flashStatus(err.detail || '请求失败', false)
    } finally {
      cmdSubmittingRef.current = false
      setCmdSubmitting(false)
    }
  }

  useEffect(() => {
    const handlePageAction = (event: Event) => {
      const detail = (event as CustomEvent).detail || {}
      if (detail.message) {
        flashStatus(String(detail.message), Boolean(detail.ok))
      }
      if (detail.action_id && detail.can_undo) {
        setLastAction({
          id: String(detail.action_id),
          type: String(detail.action_type || detail.action || 'operation'),
        })
      }
    }
    window.addEventListener('web-action-completed', handlePageAction as EventListener)
    return () => window.removeEventListener('web-action-completed', handlePageAction as EventListener)
  }, [])

  const handleUndo = async () => {
    if (!lastAction) return
    setUndoing(true)
    try {
      const res = await postUndo(lastAction.id)
      flashStatus(res.message, res.ok)
      if (res.ok) {
        setLastAction(null)
        refreshDashboard()
      }
    } catch (err: any) {
      flashStatus(err.detail || '撤回失败', false)
    } finally {
      setUndoing(false)
    }
  }

  const handleProposalDecision = async (decision: 'accept' | 'reject') => {
    if (!lastProposal) return
    setDecidingProposal(decision)
    try {
      const res = await postProposalDecision(lastProposal, decision)
      flashStatus(res.message, res.ok)
      if (res.ok || decision === 'reject') {
        setLastProposal(null)
      }
      if (res.ok) {
        refreshDashboard()
      }
    } catch (err: any) {
      flashStatus(err.detail || '提案处理失败', false)
    } finally {
      setDecidingProposal(null)
    }
  }

  const handleCmdSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!cmdText.trim()) return
    executeAction(cmdText.trim())
    setCmdText('')
    setComposerOpen(false)
  }

  const tabs = [
    { key: 'overview', label: '今日', icon: 'Sunrise', section: 'primary' as const },
    { key: 'tasks', label: '任务', icon: 'CheckSquare', section: 'primary' as const },
    { key: 'timeline', label: '时间', icon: 'Clock3', section: 'primary' as const },
    { key: 'review', label: '复盘', icon: 'NotebookPen', section: 'primary' as const },
    { key: 'fitness', label: '健身', icon: 'Dumbbell', section: 'area' as const },
    { key: 'finance', label: '财务', icon: 'WalletCards', section: 'area' as const },
    { key: 'system', label: '系统', icon: 'Settings2', section: 'system' as const },
  ]

  const currentKey = location.pathname.replace('/app/', '') || 'overview'
  const routeMotion = ROUTE_MOTION[currentKey] || ROUTE_MOTION.overview
  const activeShortcuts = QUICK_SHORTCUTS
    .filter(shortcut => !shortcut.routes || shortcut.routes.includes(currentKey))
    .slice(0, 6)

  useEffect(() => {
    const main = mainRef.current
    const progress = scrollProgressRef.current
    if (!main || !progress) return

    const updateProgress = () => {
      const max = Math.max(main.scrollHeight - main.clientHeight, 1)
      progress.style.setProperty('--scroll-progress', `${Math.min(main.scrollTop / max, 1)}`)
    }
    updateProgress()
    main.addEventListener('scroll', updateProgress, { passive: true })
    return () => main.removeEventListener('scroll', updateProgress)
  }, [])

  useEffect(() => {
    const main = mainRef.current
    if (!main) return
    main.scrollTop = 0
    scrollProgressRef.current?.style.setProperty('--scroll-progress', '0')

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const selector = [
      '.hero-card',
      '.focus-card',
      '.next-card',
      '.page-header',
      '.page-section-head',
      '.overview-flow-card',
      '.area-card',
      '.capture-panel',
      '.task-capture',
      '.task-group',
      '.card',
    ].join(',')
    let revealIndex = 0
    const observer = reducedMotion || typeof IntersectionObserver === 'undefined'
      ? null
      : new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return
          entry.target.classList.add('is-visible')
          observer?.unobserve(entry.target)
        })
      }, { threshold: 0.08, root: main, rootMargin: '0px 0px -7% 0px' })

    const registerRevealTargets = () => {
      const elements = Array.from(main.querySelectorAll<HTMLElement>(selector))
      elements.forEach(element => {
        if (element.classList.contains('motion-reveal')) return
        element.classList.add('motion-reveal')
        element.style.setProperty('--motion-order', String(revealIndex % 6))
        revealIndex += 1
        if (observer) observer.observe(element)
        else element.classList.add('is-visible')
      })
    }

    registerRevealTargets()
    const mutationObserver = new MutationObserver(registerRevealTargets)
    mutationObserver.observe(main, { childList: true, subtree: true })
    return () => {
      mutationObserver.disconnect()
      observer?.disconnect()
    }
  }, [location.pathname])

  useEffect(() => {
    const aura = pointerAuraRef.current
    if (!aura || !window.matchMedia('(pointer: fine)').matches) return

    const handlePointerMove = (event: PointerEvent) => {
      aura.style.setProperty('--pointer-x', `${event.clientX}px`)
      aura.style.setProperty('--pointer-y', `${event.clientY}px`)
      const interactive = event.target instanceof Element &&
        Boolean(event.target.closest('button, a, summary, input, select, textarea'))
      aura.classList.toggle('is-active', interactive)
      aura.classList.add('is-visible')
    }
    const handlePointerLeave = () => aura.classList.remove('is-visible')

    window.addEventListener('pointermove', handlePointerMove, { passive: true })
    document.documentElement.addEventListener('mouseleave', handlePointerLeave)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      document.documentElement.removeEventListener('mouseleave', handlePointerLeave)
    }
  }, [])

  const handleNavigate = (key: string) => {
    setComposerOpen(false)
    navigate(`/app/${key === 'overview' ? '' : key}`)
  }

  const handleShortcut = (shortcut: QuickShortcut) => {
    executeAction(shortcut.text, shortcut.action)
  }

  return (
    <div className="app-shell">
      <div
        className="route-transition"
        data-tone={routeMotion.tone}
        key={location.pathname}
        aria-hidden="true"
      >
        <span>{routeMotion.label}</span>
        <small>COGNITIVE OS</small>
      </div>
      <div className="pointer-aura" ref={pointerAuraRef} aria-hidden="true" />
      <div className="scroll-progress" ref={scrollProgressRef} aria-hidden="true"><span /></div>

      {/* Install prompt banner */}
      <div className={`install-prompt ${showInstall ? 'visible' : ''}`}>
        <span className="install-prompt-text">将此应用安装到桌面，获得更好的体验</span>
        <div className="install-prompt-actions">
          <button className="install-prompt-btn dismiss" onClick={dismissInstall}>以后再说</button>
          <button className="install-prompt-btn install" onClick={handleInstall}>安装</button>
        </div>
      </div>

      {/* Window Controls Overlay title bar (drag region for PWA) */}
      <div className="wco-titlebar">
        <span className="wco-title">Cognitive OS</span>
      </div>

      <NavDock
        tabs={tabs}
        activeKey={currentKey}
        onNavigate={handleNavigate}
        onLogout={async () => { await logout(); window.location.reload() }}
      />
      <main className="main-content" ref={mainRef}>
        {cmdStatus && (
          <div className={`cmd-toast ${cmdStatus.ok ? 'ok' : 'err'}`} role="status" aria-live="polite">
            {cmdStatus.msg}
          </div>
        )}
        {lastProposal && (
          <div className="cmd-proposal-card">
            <div>
              <div className="cmd-card-kicker">日历提案</div>
              <div className="cmd-card-title">{lastProposal.action_payload?.title || '未命名安排'}</div>
              <div className="cmd-card-meta">
                {formatProposalTime(lastProposal.action_payload?.start)}
                {lastProposal.action_payload?.end ? ` - ${formatProposalTime(lastProposal.action_payload.end)}` : ''}
              </div>
              <div className="cmd-card-note">尚未写入 Google Calendar。需要确认写入入口后才会真正创建日程。</div>
            </div>
            <div className="cmd-card-actions">
              <button
                className="cmd-mini-btn primary"
                onClick={() => handleProposalDecision('accept')}
                disabled={decidingProposal !== null}
              >
                {decidingProposal === 'accept' ? '写入中' : '接受并写入日历'}
              </button>
              <button
                className="cmd-mini-btn danger"
                onClick={() => handleProposalDecision('reject')}
                disabled={decidingProposal !== null}
              >
                {decidingProposal === 'reject' ? '拒绝中' : '拒绝'}
              </button>
              <button className="cmd-mini-btn" onClick={() => setLastProposal(null)} disabled={decidingProposal !== null}>
                关闭
              </button>
            </div>
          </div>
        )}
        {lastAction && (
          <div className="cmd-action-card">
            <div>
              <div className="cmd-card-kicker">最近操作</div>
              <div className="cmd-card-title">{actionTypeLabel(lastAction.type)} 可撤回</div>
              <div className="cmd-card-note">撤回会发布事件修正状态，不直接改数据库。</div>
            </div>
            <button className="cmd-mini-btn danger" onClick={handleUndo} disabled={undoing}>
              {undoing ? '撤回中' : '撤回'}
            </button>
          </div>
        )}
        <Routes>
          <Route path="/app" element={<Overview onAction={executeAction} />} />
          <Route path="/app/" element={<Overview onAction={executeAction} />} />
          <Route path="/app/overview" element={<Overview onAction={executeAction} />} />
          <Route path="/app/timeline" element={<TimelinePage />} />
          <Route path="/app/tasks" element={<Tasks onAction={executeAction} />} />
          <Route path="/app/fitness" element={<Fitness />} />
          <Route path="/app/finance" element={<Finance onAction={executeAction} />} />
          <Route path="/app/review" element={<Review onAction={executeAction} />} />
          <Route path="/app/system" element={<SystemPage onAction={executeAction} />} />
        </Routes>
      </main>
      <BottomNav tabs={tabs} activeKey={currentKey} onNavigate={handleNavigate} />
      <button
        className="mobile-capture-trigger"
        onClick={() => setComposerOpen(open => !open)}
        aria-expanded={composerOpen}
        aria-controls="global-command-composer"
      >
        {composerOpen ? '关闭' : '记录'}
      </button>
      {/* Global command composer */}
      {composerOpen && <div className="cmd-composer-backdrop" onClick={() => setComposerOpen(false)} />}
      <div id="global-command-composer" className={`cmd-composer-bar ${composerOpen ? 'mobile-open' : ''}`}>
        <div className="cmd-composer-heading">
          <div>
            <div className="cmd-composer-kicker">CAPTURE</div>
            <div className="cmd-composer-title">记录此刻</div>
          </div>
          <button className="cmd-composer-close" onClick={() => setComposerOpen(false)} type="button">关闭</button>
        </div>
        {activeShortcuts.length > 0 && (
          <div className="cmd-quickbar">
            {activeShortcuts.map(shortcut => (
              <button
                key={`${shortcut.label}-${shortcut.action || shortcut.text}`}
                type="button"
                className="cmd-quick-chip"
                onClick={() => handleShortcut(shortcut)}
                disabled={cmdSubmitting}
              >
                {shortcut.label}
              </button>
            ))}
          </div>
        )}
        <form onSubmit={handleCmdSubmit} className="cmd-composer-form">
          <input
            type="text"
            className="cmd-composer-input"
            placeholder="完成画画 30min / 补水500 / 今天状态差 / 同步刷新数据..."
            value={cmdText}
            onChange={e => setCmdText(e.target.value)}
          />
          <button type="submit" className="cmd-composer-btn" disabled={!cmdText.trim() || cmdSubmitting}>
            {cmdSubmitting ? '处理中' : '发送'}
          </button>
        </form>
      </div>
    </div>
  )
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

function actionTypeLabel(type: string) {
  const labels: Record<string, string> = {
    finance_transaction: '消费记录',
    finance_income: '收入记录',
  }
  return labels[type] || '操作'
}

function AppContent({ onLogout }: { onLogout: () => void }) {
  return (
    <BrowserRouter>
      <MainShell />
    </BrowserRouter>
  )
}

// ── PWA Install Prompt ─────────────────────────────────────────
let deferredInstallPrompt: any = null
let installPromptShown = false

function useInstallPrompt() {
  const [showPrompt, setShowPrompt] = useState(false)

  useEffect(() => {
    const handler = (e: Event) => {
      e.preventDefault()
      deferredInstallPrompt = e
      if (!installPromptShown) {
        setShowPrompt(true)
      }
    }
    window.addEventListener('beforeinstallprompt', handler)

    // Check if already installed
    if (window.matchMedia('(display-mode: standalone)').matches) {
      installPromptShown = true
    }

    return () => window.removeEventListener('beforeinstallprompt', handler)
  }, [])

  const install = async () => {
    if (!deferredInstallPrompt) return
    deferredInstallPrompt.prompt()
    const { outcome } = await deferredInstallPrompt.userChoice
    deferredInstallPrompt = null
    installPromptShown = true
    setShowPrompt(false)
    console.log('[PWA] Install outcome:', outcome)
  }

  const dismiss = () => {
    deferredInstallPrompt = null
    installPromptShown = true
    setShowPrompt(false)
  }

  return { showPrompt, install, dismiss }
}

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)

  useEffect(() => {
    checkAuth().then(setAuthenticated)
  }, [])

  if (authenticated === null) {
    return (
      <div className="loading-screen">
        <div className="loading-spinner" />
      </div>
    )
  }

  if (!authenticated) {
    return <LoginScreen onLogin={() => setAuthenticated(true)} />
  }

  return <AppContent onLogout={() => setAuthenticated(false)} />
}

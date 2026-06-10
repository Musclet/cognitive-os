import React, { useEffect, useState } from 'react'
import { getDashboard, DashboardData, postFinanceAction, postFinanceRevert, FinanceActionRequest } from '../api'
import { Ring } from '../components/Ring'
import { announceWebAction, refreshDashboard } from '../events'

interface Props { onAction?: (text: string, action?: string) => void }

type TabKey = 'expense' | 'income' | 'parent' | 'debt'
type ParentMode = 'received' | 'plan'

const TAB_LABELS: Record<TabKey, string> = {
  expense: '支出',
  income: '收入',
  parent: '找爸妈/要钱计划',
  debt: '对象欠款',
}

interface FormState {
  amount: string
  category: string
  description: string
  source: string
  person: string
  requested_date: string
  item_id: string
  date: string
  counterparty: string
}

const EMPTY_FORM: FormState = {
  amount: '', category: 'other', description: '',
  source: '', person: '', requested_date: '', item_id: '',
  date: '', counterparty: '',
}

const CATEGORY_OPTIONS = [
  { value: 'outing', label: '约会/出去玩' },
  { value: 'necessary', label: '必要开销' },
  { value: 'emotional', label: '情绪消费' },
  { value: 'fitness_health', label: '健身/健康' },
  { value: 'art_learning_investment', label: '学习/画材' },
  { value: 'system_subscription', label: '固定订阅' },
  { value: 'other', label: '其他' },
]

export function Finance({ onAction }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [activeTab, setActiveTab] = useState<TabKey>('expense')
  const [parentMode, setParentMode] = useState<ParentMode>('received')
  const [form, setForm] = useState<FormState>({ ...EMPTY_FORM })
  const [status, setStatus] = useState<{ msg: string; ok: boolean } | null>(null)
  const [loading, setLoading] = useState(false)
  const [revertingId, setRevertingId] = useState('')

  const loadDashboard = () => {
    getDashboard().then(setData).catch(() => {})
  }

  useEffect(() => {
    loadDashboard()
    const handler = () => loadDashboard()
    window.addEventListener('dashboard-refresh', handler)
    return () => window.removeEventListener('dashboard-refresh', handler)
  }, [])

  const flash = (msg: string, ok: boolean) => {
    setStatus({ msg, ok })
    setTimeout(() => setStatus(null), 3000)
  }

  const computeAction = (tab: TabKey): FinanceActionRequest['action'] => {
    if (tab === 'parent') {
      return parentMode === 'plan' ? 'parent_plan' : 'parent_received'
    }
    if (tab === 'debt') {
      return 'partner_debt'
    }
    return tab
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const amount = parseFloat(form.amount)
    if (!amount || amount <= 0) { flash('请输入有效金额', false); return }

    setLoading(true)
    try {
      const action = computeAction(activeTab)
      const req: FinanceActionRequest = { action, amount }
      if (activeTab === 'expense') {
        req.category = form.category || 'other'
        req.description = form.description || undefined
      } else if (activeTab === 'income') {
        req.source = form.source || '其他'
        req.description = form.description || undefined
      } else if (activeTab === 'parent') {
        req.person = form.person || undefined
        req.description = form.description || undefined
        req.requested_date = form.requested_date || undefined
        req.item_id = form.item_id || undefined
        req.category = form.category || 'other'
      } else if (activeTab === 'debt') {
        req.description = form.description || undefined
        req.date = form.date || undefined
        req.counterparty = form.counterparty || '对象'
      }

      const res = await postFinanceAction(req)
      flash(res.message, res.ok)
      announceWebAction({
        ok: res.ok,
        message: res.message,
        action: res.action,
        action_id: res.action_id || null,
        action_type: action === 'expense' ? 'finance_transaction' : action === 'income' ? 'finance_income' : `finance_${action}`,
        can_undo: Boolean(res.can_undo && res.action_id),
      })
      if (res.ok && res.dashboard) {
        setData(res.dashboard)
        setForm({ ...EMPTY_FORM, category: 'other' })
        refreshDashboard()
      }
    } catch (err: any) {
      flash(err.detail || '请求失败', false)
    } finally {
      setLoading(false)
    }
  }

  const set = (field: keyof FormState, value: string) =>
    setForm(prev => ({ ...prev, [field]: value }))

  const handleLedgerUndo = async (entry: any, actionType: 'finance_transaction' | 'finance_income') => {
    const actionId = String(entry.action_id || '')
    const amount = Number(entry.amount || 0)
    if (!actionId || !amount || !entry.can_undo) {
      flash('这条旧账目不能直接撤回。', false)
      return
    }
    setRevertingId(actionId)
    try {
      const res = await postFinanceRevert({
        action_type: actionType,
        action_id: actionId,
        amount,
        category: entry.category || 'other',
      })
      flash(res.message, res.ok)
      announceWebAction({
        ok: res.ok,
        message: res.message,
        action: 'finance_revert',
        action_id: actionId,
        action_type: actionType,
        can_undo: false,
      })
      if (res.ok && res.dashboard) {
        setData(res.dashboard)
        refreshDashboard()
      }
    } catch (err: any) {
      flash(err.detail || '撤回失败', false)
    } finally {
      setRevertingId('')
    }
  }

  if (!data) return <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-dim)' }}>加载中...</div>

  const fin = data.finance || {}
  const parentFunds = data.parent_funds || ({} as any)
  const partnerDebts = data.partner_debts || ({} as any)

  const budget = fin.monthly_budget || 0
  const spend = fin.monthly_spend || 0
  const savingsTarget = fin.savings_target || 0
  const savingsProgress = fin.savings_progress || 0
  const partnerDebtTotal = partnerDebts.total_outstanding || fin.partner_debt || 0

  return (
    <div>
      <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>财务</h2>

      {/* Summary */}
      <div className="finance-summary">
        <div className="finance-stat">
          <div className="num" style={{ color: spend > budget ? 'var(--danger)' : 'var(--accent-gold)' }}>
            ¥{spend}
          </div>
          <div className="lbl">已消费</div>
        </div>
        <div className="finance-stat">
          <div className="num" style={{ color: 'var(--accent-green)' }}>¥{Math.max(0, budget - spend)}</div>
          <div className="lbl">剩余预算</div>
        </div>
        <div className="finance-stat">
          <div className="num" style={{ color: 'var(--accent-pink)' }}>¥{savingsProgress}</div>
          <div className="lbl">储蓄</div>
        </div>
        <div className="finance-stat">
          <div className="num" style={{ color: partnerDebtTotal > 0 ? 'var(--accent-berry)' : 'var(--text-dim)' }}>
            ¥{partnerDebtTotal}
          </div>
          <div className="lbl">伙伴债务</div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="fin-tabs">
        {(Object.keys(TAB_LABELS) as TabKey[]).map(tab => (
          <button
            key={tab}
            className={`fin-tab${activeTab === tab ? ' active' : ''}`}
            onClick={() => {
              setActiveTab(tab)
              setParentMode('received')
              setForm({ ...EMPTY_FORM, category: 'other' })
              setStatus(null)
            }}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {/* Status toast */}
      {status && (
        <div style={{
          padding: '8px 14px', borderRadius: 10, marginBottom: 12, fontSize: 13,
          background: status.ok ? 'rgba(125,184,125,0.2)' : 'rgba(212,90,90,0.2)',
          color: status.ok ? 'var(--accent-green)' : 'var(--danger)',
        }}>
          {status.msg}
        </div>
      )}

      {/* Forms */}
      <form onSubmit={handleSubmit} className="fin-form">
        {activeTab === 'expense' && (
          <>
            <div className="fin-field">
              <label className="fin-label">金额 (¥)</label>
              <input className="fin-input" type="number" step="0.01" min="0.01" placeholder="18" value={form.amount}
                onChange={e => set('amount', e.target.value)} required />
            </div>
            <div className="fin-field">
              <label className="fin-label">分类</label>
              <select className="fin-input fin-select" value={form.category}
                onChange={e => set('category', e.target.value)}>
                {CATEGORY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div className="fin-field">
              <label className="fin-label">备注</label>
              <input className="fin-input" type="text" placeholder="奶茶 / 午饭..." value={form.description}
                onChange={e => set('description', e.target.value)} />
            </div>
            <button type="submit" className="fin-btn" disabled={loading || !form.amount}>
              {loading ? '提交中...' : '记录支出'}
            </button>
          </>
        )}

        {activeTab === 'income' && (
          <>
            <div className="fin-field">
              <label className="fin-label">金额 (¥)</label>
              <input className="fin-input" type="number" step="0.01" min="0.01" placeholder="1000" value={form.amount}
                onChange={e => set('amount', e.target.value)} required />
            </div>
            <div className="fin-field">
              <label className="fin-label">来源</label>
              <input className="fin-input" type="text" placeholder="生活费 / 兼职..." value={form.source}
                onChange={e => set('source', e.target.value)} />
            </div>
            <div className="fin-field">
              <label className="fin-label">备注</label>
              <input className="fin-input" type="text" placeholder="生活费到账" value={form.description}
                onChange={e => set('description', e.target.value)} />
            </div>
            <button type="submit" className="fin-btn" disabled={loading || !form.amount}>
              {loading ? '提交中...' : '记录收入'}
            </button>
          </>
        )}

        {activeTab === 'parent' && (
          <>
            <div className="fin-tabs compact">
              <button
                type="button"
                className={`fin-tab${parentMode === 'received' ? ' active' : ''}`}
                onClick={() => { setParentMode('received'); set('requested_date', '') }}
              >
                记录到账
              </button>
              <button
                type="button"
                className={`fin-tab${parentMode === 'plan' ? ' active' : ''}`}
                onClick={() => setParentMode('plan')}
              >
                计划要钱
              </button>
            </div>
            <div className="fin-field">
              <label className="fin-label">金额 (¥)</label>
              <input className="fin-input" type="number" step="0.01" min="0.01" placeholder="150" value={form.amount}
                onChange={e => set('amount', e.target.value)} required />
            </div>
            <div className="fin-field">
              <label className="fin-label">对象 (爸爸/妈妈)</label>
              <input className="fin-input" type="text" placeholder="爸爸" value={form.person}
                onChange={e => set('person', e.target.value)} />
            </div>
            <div className="fin-field">
              <label className="fin-label">用途</label>
              <input className="fin-input" type="text" placeholder="买画材 / 话费..." value={form.description}
                onChange={e => set('description', e.target.value)} />
            </div>
            {parentMode === 'plan' && (
              <div className="fin-field">
                <label className="fin-label">计划日期</label>
                <input className="fin-input" type="date" value={form.requested_date}
                  onChange={e => set('requested_date', e.target.value)} />
              </div>
            )}
            <button type="submit" className="fin-btn" disabled={loading || !form.amount || (parentMode === 'plan' && !form.requested_date)}>
              {loading ? '提交中...' : (parentMode === 'plan' ? '记录要钱计划' : '记录到账')}
            </button>
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 6 }}>
              {parentMode === 'plan' ? '记录一条未来要钱计划。' : '记录已经到账的钱，会同步计入收入。'}
            </div>
          </>
        )}

        {activeTab === 'debt' && (
          <>
            <div className="fin-field">
              <label className="fin-label">金额 (¥)</label>
              <input className="fin-input" type="number" step="0.01" min="0.01" placeholder="500" value={form.amount}
                onChange={e => set('amount', e.target.value)} required />
            </div>
            <div className="fin-field">
              <label className="fin-label">对方</label>
              <input className="fin-input" type="text" placeholder="对象" value={form.counterparty}
                onChange={e => set('counterparty', e.target.value)} />
            </div>
            <div className="fin-field">
              <label className="fin-label">备注</label>
              <input className="fin-input" type="text" placeholder="借给对象..." value={form.description}
                onChange={e => set('description', e.target.value)} />
            </div>
            <div className="fin-field">
              <label className="fin-label">日期</label>
              <input className="fin-input" type="date" value={form.date}
                onChange={e => set('date', e.target.value)} />
            </div>
            <button type="submit" className="fin-btn" disabled={loading || !form.amount}>
              {loading ? '提交中...' : '记录欠款'}
            </button>
          </>
        )}
      </form>

      {/* Detail panels */}
      <div className="cards-grid" style={{ marginTop: 16 }}>
        {activeTab === 'expense' && fin.by_category && Object.keys(fin.by_category).length > 0 && (
          <div className="card">
            <h3>📊 支出分类</h3>
            {Object.entries(fin.by_category as Record<string, number>)
              .sort(([, a], [, b]) => b - a)
              .map(([cat, amt]) => (
                <div key={cat} className="stat-row">
                  <span className="label">{catLabel(cat)}</span>
                  <span className="value">¥{amt}</span>
                </div>
              ))}
            <div className="stat-row" style={{ borderTop: '1px solid rgba(255,255,255,0.06)', marginTop: 6, paddingTop: 8 }}>
              <span className="label">总支出</span>
              <span className="value">¥{fin.outflow || 0}</span>
            </div>
          </div>
        )}

        {activeTab === 'expense' && fin.transactions?.length > 0 && (
          <div className="card finance-ledger-card">
            <h3>最近支出</h3>
            {fin.transactions.slice().reverse().map((tx: any, i: number) => (
              <LedgerRow
                key={tx.action_id || `${tx.timestamp}-${i}`}
                entry={tx}
                title={tx.description || catLabel(tx.category || 'other')}
                meta={`${catLabel(tx.category || 'other')} · ${formatLedgerTime(tx.timestamp)}`}
                amountPrefix="-"
                reverting={revertingId === tx.action_id}
                onUndo={() => handleLedgerUndo(tx, 'finance_transaction')}
              />
            ))}
          </div>
        )}

        {activeTab === 'income' && fin.inflow !== undefined && (
          <div className="card">
            <h3>💰 本月收入</h3>
            <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--accent-green)', textAlign: 'center', margin: '12px 0' }}>
              ¥{fin.inflow}
            </div>
            <div className="stat-row">
              <span className="label">支出</span>
              <span className="value">¥{fin.outflow || 0}</span>
            </div>
            <div className="stat-row">
              <span className="label">预计储蓄</span>
              <span className="value" style={{ color: 'var(--accent-pink)' }}>¥{fin.estimated_savings || 0}</span>
            </div>
          </div>
        )}

        {activeTab === 'income' && fin.income_log?.length > 0 && (
          <div className="card finance-ledger-card">
            <h3>最近收入</h3>
            {fin.income_log.slice().reverse().map((inc: any, i: number) => (
              <LedgerRow
                key={inc.action_id || `${inc.timestamp}-${i}`}
                entry={inc}
                title={inc.description || inc.source || '收入'}
                meta={`${inc.source || '其他'} · ${formatLedgerTime(inc.timestamp)}`}
                amountPrefix="+"
                reverting={revertingId === inc.action_id}
                onUndo={() => handleLedgerUndo(inc, 'finance_income')}
              />
            ))}
          </div>
        )}

        {activeTab === 'parent' && (
          <ParentFundPanel data={parentFunds} />
        )}

        {activeTab === 'debt' && (
          <PartnerDebtPanel data={partnerDebts} />
        )}
      </div>

      {/* Old summary cards (hidden since tab panel shows relevant data) */}
    </div>
  )
}

function catLabel(cat: string): string {
  const map: Record<string, string> = {
    outing: '约会/出去玩', necessary: '必要开销', emotional: '情绪消费',
    fitness_health: '健身/健康', art_learning_investment: '学习/画材',
    system_subscription: '固定订阅', other: '其他',
  }
  return map[cat] || cat
}

function formatLedgerTime(value?: string): string {
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

function LedgerRow({
  entry,
  title,
  meta,
  amountPrefix,
  reverting,
  onUndo,
}: {
  entry: any;
  title: string;
  meta: string;
  amountPrefix: string;
  reverting: boolean;
  onUndo: () => void;
}) {
  const canUndo = Boolean(entry.can_undo)
  return (
    <div className={`finance-ledger-row${entry.reverted ? ' reverted' : ''}`}>
      <div className="finance-ledger-main">
        <div className="finance-ledger-title">{title}</div>
        <div className="finance-ledger-meta">{meta}</div>
      </div>
      <div className="finance-ledger-amount">{amountPrefix}¥{entry.amount || 0}</div>
      <button
        type="button"
        className="recent-action-undo"
        disabled={!canUndo || reverting}
        onClick={onUndo}
        title={entry.action_id ? (entry.reverted ? '已撤回' : '撤回这条账目') : '旧账目缺少事件 id，不能直接撤回'}
      >
        {entry.reverted ? '已撤回' : reverting ? '撤回中' : canUndo ? '撤回' : '不可撤回'}
      </button>
    </div>
  )
}

function ParentFundPanel({ data }: { data: any }) {
  if (!data || (!data.planned_requests?.length && !data.request_log?.length && !data.received_log?.length && !data.recurring_items?.length)) {
    return (
      <div className="card">
        <h3>📋 父母资助</h3>
        <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>暂无记录</div>
      </div>
    )
  }

  return (
    <>
      {data.recurring_items?.length > 0 && (
        <div className="card">
          <h3>🔄 固定项目</h3>
          {data.recurring_items.map((item: any, i: number) => (
            <div key={i} className="stat-row">
              <span className="label">{item.label || item.item_id}</span>
              <span className="value">¥{item.amount}</span>
            </div>
          ))}
        </div>
      )}
      {data.planned_requests?.length > 0 && (
        <div className="card">
          <h3>📅 计划要钱</h3>
          {data.planned_requests.slice(-5).reverse().map((r: any, i: number) => (
            <div key={i} className="stat-row">
              <span className="label">{r.description || '计划'}</span>
              <span className="value">¥{r.amount}</span>
            </div>
          ))}
        </div>
      )}
      {data.request_log?.length > 0 && (
        <div className="card">
          <h3>📜 请求记录</h3>
          {data.request_log.slice(-5).reverse().map((r: any, i: number) => (
            <div key={i} className="stat-row">
              <span className="label">{r.description || '请求'}</span>
              <span className="value">¥{r.amount}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

function PartnerDebtPanel({ data }: { data: any }) {
  if (!data || (!data.debts?.length && !data.total_outstanding)) {
    return (
      <div className="card">
        <h3>🤝 伙伴债务</h3>
        <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>暂无欠款记录</div>
      </div>
    )
  }

  return (
    <>
      <div className="card">
        <h3>📊 债务概览</h3>
        <div style={{ textAlign: 'center', margin: '12px 0' }}>
          <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--accent-berry)' }}>
            ¥{data.total_outstanding || 0}
          </div>
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-dim)', textAlign: 'center' }}>未偿还总额</div>
      </div>
      {data.debts?.length > 0 && (
        <div className="card">
          <h3>📜 欠款记录</h3>
          {data.debts.slice(-5).reverse().map((d: any, i: number) => (
            <div key={i} className="stat-row">
              <span className="label">{d.description || d.counterparty || '欠款'}</span>
              <span className="value">¥{d.amount}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

import * as Icons from 'lucide-react'
import React from 'react'

interface Tab {
  key: string
  label: string
  icon: string
  section?: 'primary' | 'area' | 'system'
}

export function NavDock({
  tabs,
  activeKey,
  onNavigate,
  onLogout,
}: {
  tabs: Tab[]
  activeKey: string
  onNavigate: (k: string) => void
  onLogout: () => void
}) {
  const primary = tabs.filter(tab => tab.section === 'primary')
  const areas = tabs.filter(tab => tab.section === 'area')
  const system = tabs.filter(tab => tab.section === 'system')

  const renderTabs = (items: Tab[]) => items.map(t => {
    const IconComp = (Icons as any)[t.icon] || Icons.Circle
    return (
      <button
        key={t.key}
        className={`nav-dock-item ${activeKey === t.key ? 'active' : ''}`}
        onClick={() => onNavigate(t.key)}
        aria-current={activeKey === t.key ? 'page' : undefined}
      >
        <IconComp size={19} />
        <span>{t.label}</span>
      </button>
    )
  })

  return (
    <nav className="nav-dock">
      <div className="nav-brand">
        <div className="nav-brand-mark">CO</div>
        <div>
          <div className="nav-brand-name">Cognitive OS</div>
          <div className="nav-brand-subtitle">Personal thinking space</div>
        </div>
      </div>

      <div className="nav-section">
        <div className="nav-section-label">工作区</div>
        {renderTabs(primary)}
      </div>

      <div className="nav-section">
        <div className="nav-section-label">领域</div>
        {renderTabs(areas)}
      </div>

      <div className="nav-section nav-section-system">
        {renderTabs(system)}
        <button className="nav-dock-item" onClick={onLogout}>
          <Icons.LogOut size={19} />
          <span>退出</span>
        </button>
      </div>
    </nav>
  )
}

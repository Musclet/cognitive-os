import * as Icons from 'lucide-react'
import React from 'react'

interface Tab {
  key: string
  label: string
  icon: string
}

export function NavDock({ tabs, activeKey, onNavigate }: { tabs: Tab[]; activeKey: string; onNavigate: (k: string) => void }) {
  return (
    <nav className="nav-dock">
      {tabs.map(t => {
        const IconComp = (Icons as any)[t.icon] || Icons.Circle
        return (
          <button
            key={t.key}
            className={`nav-dock-item ${activeKey === t.key ? 'active' : ''}`}
            onClick={() => onNavigate(t.key)}
            title={t.label}
          >
            <IconComp size={20} />
            <span>{t.label}</span>
          </button>
        )
      })}
    </nav>
  )
}

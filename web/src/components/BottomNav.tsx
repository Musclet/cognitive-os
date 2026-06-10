import * as Icons from 'lucide-react'
import React from 'react'

interface Tab {
  key: string
  label: string
  icon: string
}

export function BottomNav({ tabs, activeKey, onNavigate }: { tabs: Tab[]; activeKey: string; onNavigate: (k: string) => void }) {
  // Show only first 5 for mobile
  const mobile = tabs.slice(0, 5)
  return (
    <nav className="bottom-nav">
      {mobile.map(t => {
        const IconComp = (Icons as any)[t.icon] || Icons.Circle
        return (
          <button
            key={t.key}
            className={`bottom-nav-item ${activeKey === t.key ? 'active' : ''}`}
            onClick={() => onNavigate(t.key)}
          >
            <IconComp size={20} />
            <span>{t.label}</span>
          </button>
        )
      })}
    </nav>
  )
}

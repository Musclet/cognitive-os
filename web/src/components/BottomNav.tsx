import * as Icons from 'lucide-react'
import React, { useState } from 'react'

interface Tab {
  key: string
  label: string
  icon: string
  section?: 'primary' | 'area' | 'system'
}

export function BottomNav({ tabs, activeKey, onNavigate }: { tabs: Tab[]; activeKey: string; onNavigate: (k: string) => void }) {
  const [moreOpen, setMoreOpen] = useState(false)
  const mobileKeys = ['overview', 'tasks', 'timeline']
  const mobile = mobileKeys
    .map(key => tabs.find(tab => tab.key === key))
    .filter((tab): tab is Tab => Boolean(tab))
  const more = tabs.filter(tab => !mobileKeys.includes(tab.key))

  const navigate = (key: string) => {
    setMoreOpen(false)
    onNavigate(key)
  }

  return (
    <>
      {moreOpen && (
        <div className="mobile-more-backdrop" onClick={() => setMoreOpen(false)}>
          <div className="mobile-more-sheet" onClick={event => event.stopPropagation()}>
            <div className="mobile-more-head">
              <div>
                <div className="mobile-more-kicker">Cognitive OS</div>
                <div className="mobile-more-title">更多空间</div>
              </div>
              <button className="mobile-more-close" onClick={() => setMoreOpen(false)} aria-label="关闭更多菜单">
                <Icons.X size={20} />
              </button>
            </div>
            <div className="mobile-more-grid">
              {more.map(tab => {
                const IconComp = (Icons as any)[tab.icon] || Icons.Circle
                return (
                  <button
                    key={tab.key}
                    className={`mobile-more-item ${activeKey === tab.key ? 'active' : ''}`}
                    onClick={() => navigate(tab.key)}
                  >
                    <IconComp size={21} />
                    <span>{tab.label}</span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}

      <nav className="bottom-nav">
        {mobile.map(t => {
          const IconComp = (Icons as any)[t.icon] || Icons.Circle
          return (
            <button
              key={t.key}
              className={`bottom-nav-item ${activeKey === t.key ? 'active' : ''}`}
              onClick={() => navigate(t.key)}
              aria-current={activeKey === t.key ? 'page' : undefined}
            >
              <IconComp size={20} />
              <span>{t.label}</span>
            </button>
          )
        })}
        <button
          className={`bottom-nav-item ${more.some(tab => tab.key === activeKey) ? 'active' : ''}`}
          onClick={() => setMoreOpen(true)}
        >
          <Icons.Menu size={20} />
          <span>更多</span>
        </button>
      </nav>
    </>
  )
}

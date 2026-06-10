import React from 'react'

interface RingProps {
  pct: number
  size?: number
  strokeWidth?: number
  color?: string
}

export function Ring({ pct, size = 36, strokeWidth = 3, color = 'pink' }: RingProps) {
  const r = (size - strokeWidth) / 2
  const circ = 2 * Math.PI * r
  const offset = circ - (Math.min(pct, 100) / 100) * circ
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle className="ring-bg" cx={size / 2} cy={size / 2} r={r} strokeWidth={strokeWidth} />
      <circle
        className={`ring-fill ${color}`}
        cx={size / 2} cy={size / 2} r={r}
        strokeWidth={strokeWidth}
        strokeDasharray={circ}
        strokeDashoffset={offset}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  )
}

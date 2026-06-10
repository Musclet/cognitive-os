interface WebActionCompletedDetail {
  ok: boolean
  message?: string
  action?: string
  action_id?: string | null
  action_type?: string
  can_undo?: boolean
}

export function announceWebAction(detail: WebActionCompletedDetail) {
  window.dispatchEvent(new CustomEvent('web-action-completed', { detail }))
}

export function refreshDashboard() {
  window.dispatchEvent(new CustomEvent('dashboard-refresh'))
}

import type { Event, RobotStatus, Task } from './types'

const json = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.message || body.code || `HTTP ${response.status}`)
  }
  return response.json()
}
export const api = {
  status: () => json<RobotStatus>('/api/robot/status'),
  tasks: () => json<Task[]>('/api/tasks'),
  events: (after = 0) => json<Event[]>(`/api/events?after=${after}`),
  command: (text: string) => json<{ task: Task | null; intent: Record<string, unknown> }>('/api/commands', { method: 'POST', body: JSON.stringify({ text }) }),
  confirm: (id: string) => json<Task>(`/api/tasks/${id}/confirm`, { method: 'POST' }),
  cancel: (id: string) => json<Task>(`/api/tasks/${id}/cancel`, { method: 'POST' }),
  stop: () => json<RobotStatus>('/api/emergency-stop', { method: 'POST' }),
  recover: (scene?: string) => json<RobotStatus>('/api/simulation/recover', { method: 'POST', body: JSON.stringify({ scene }) }),
  reset: () => json<RobotStatus>('/api/demo/reset', { method: 'POST' }),
}

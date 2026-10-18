import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.restoreAllMocks())

describe('API client', () => {
  it('sends commands as JSON and returns the task response', async () => {
    const payload = { intent: { intent: 'move_to' }, task: { id: 'task-1', status: 'queued' } }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }))

    await expect(api.command('去客厅')).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith('/api/commands', {
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
      body: JSON.stringify({ text: '去客厅' }),
    })
  })

  it('surfaces structured backend errors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ code: 'STOP_LATCHED', message: 'Stopped' }), { status: 409 }))
    await expect(api.command('去客厅')).rejects.toThrow('Stopped')
  })
})

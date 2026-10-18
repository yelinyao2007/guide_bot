// @vitest-environment jsdom
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { App } from './main'
import { api } from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('requires explicit help confirmation and exposes emergency stop', async () => {
  vi.stubGlobal('WebSocket', class { close() {} })
  vi.spyOn(api, 'status').mockResolvedValue({ backend: 'mujoco', model_label: 'Go2', available: true, stopped: true, locomotion: false, position: [0, 0, 0], error_code: null, scene: 'help', simulation_time: 1 })
  vi.spyOn(api, 'tasks').mockResolvedValue([{ id: 'help-1', intent: { intent: 'request_help' }, status: 'awaiting_confirmation', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }])
  const confirm = vi.spyOn(api, 'confirm').mockResolvedValue({} as never)
  const stop = vi.spyOn(api, 'stop').mockResolvedValue({} as never)
  render(<App />)
  await screen.findByText('待确认')
  expect(screen.getByText('● 模拟模式 / MUJOCO')).toBeTruthy()
  expect(screen.getByText('尚不支持行走')).toBeTruthy()
  expect(confirm).not.toHaveBeenCalled()
  fireEvent.click(screen.getAllByRole('button', { name: '确认联系家人' }).at(-1)!)
  await waitFor(() => expect(confirm).toHaveBeenCalledWith('help-1'))
  fireEvent.click(screen.getByRole('button', { name: '紧急停止' }))
  await waitFor(() => expect(stop).toHaveBeenCalledOnce())
})

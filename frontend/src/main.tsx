import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Event, RobotStatus, Task } from './types'
import './style.css'

const labels: Record<string, string> = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消', timed_out: '超时', awaiting_confirmation: '待确认' }
const time = (value: string) => new Date(value).toLocaleTimeString()

const SIM_URL = `http://${window.location.hostname}:8001`
interface SimState { person_state: string; seat_occupied: boolean; speaks: { t: number; text: string }[]; sim_time: number; dog_loaded: boolean; case_running: boolean }
const personLabels: Record<string, string> = { sit: '坐着（座位占用）', stand: '站着（座位空）', away: '已离开（座位空）' }

function SimPanel() {
  const [sim, setSim] = useState<SimState | null>(null)
  useEffect(() => {
    let on = true
    const poll = async () => {
      try { const r = await fetch(`${SIM_URL}/state`); if (on) setSim(await r.json()) } catch { if (on) setSim(null) }
    }
    poll(); const timer = window.setInterval(poll, 1000)
    return () => { on = false; window.clearInterval(timer) }
  }, [])
  const act = (path: string) => async () => { try { await fetch(`${SIM_URL}${path}`, { method: 'POST' }) } catch { setSim(null) } }
  return <aside className="panel sim">
    <h2>空位仿真编排</h2>
    {!sim && <p className="muted">仿真未启动。在机器人/虚拟机上：`bash simulation/fetch_assets.sh` 后 `python3 simulation/run_sim.py`（面板每秒重试）</p>}
    {sim && <>
      <p role="status">人：{personLabels[sim.person_state] || sim.person_state} · 座位：{sim.seat_occupied ? '占用' : '空'} · 狗模型：{sim.dog_loaded ? '已加载' : '未加载'}</p>
      <div className="shortcuts">
        <button onClick={act('/person/sit')}>人坐下</button>
        <button onClick={act('/person/stand')}>人起身</button>
        <button onClick={act('/person/leave')}>人走开</button>
        <button onClick={act('/person/return')}>人回来</button>
        <button onClick={act('/case/run')}>{sim.case_running ? '案例运行中…' : '跑空位案例'}</button>
      </div>
      <div className="timeline"><h2>狗播报</h2>{sim.speaks.length === 0 && <p className="muted">暂无播报</p>}{sim.speaks.slice(-5).reverse().map((s, i) => <div className="event" key={`${s.t}-${i}`}><span>🔊</span><b>{s.text}</b><time>{new Date(s.t * 1000).toLocaleTimeString()}</time></div>)}</div>
    </>}
  </aside>
}

export function App() {
  const [status, setStatus] = useState<RobotStatus | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [reply, setReply] = useState('')
  const [connected, setConnected] = useState(false)
  const [scene, setScene] = useState('living_room')
  const refresh = useCallback(async () => { try { const [s, t] = await Promise.all([api.status(), api.tasks()]); setStatus(s); setTasks(t) } catch (e) { setError((e as Error).message) } }, [])
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 1000); return () => window.clearInterval(timer) }, [refresh])
  useEffect(() => {
    let ws: WebSocket; let retry: number; let closed = false; let after = 0
    const connect = () => {
      if (closed) return
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'; ws = new WebSocket(`${protocol}://${location.host}/ws/events?after=${after}`)
      ws.onopen = () => setConnected(true)
      ws.onmessage = (message) => {
        const event = JSON.parse(message.data) as Event
        if (event.seq <= after) return
        after = event.seq
        setEvents((old) => [...old, event].slice(-100))
        if (event.type === 'robot.state' || event.type === 'robot.recovered') setStatus(event.payload as unknown as RobotStatus)
        if (event.type === 'task.updated') {
          const task = event.payload as unknown as Task
          setTasks((old) => [...old.filter((item) => item.id !== task.id), task])
        }
        if (event.type === 'demo.reset') refresh()
      }
      ws.onclose = () => { setConnected(false); if (!closed) retry = window.setTimeout(connect, 1200) }
    }
    connect()
    return () => { closed = true; window.clearTimeout(retry); ws?.close() }
  }, [refresh])
  const run = async (action: () => Promise<unknown>) => { setError(''); try { await action(); await refresh() } catch (e) { setError((e as Error).message) } }
  const send = async (value = text) => { if (!value.trim()) return; await run(async () => { const result = await api.command(value); setReply(String(result.intent.reply || '指令已处理')) }); setText('') }
  return <main>
    <header><div><span className="eyebrow">GUARDIAN DOG</span><h1>最后一米守护者</h1></div><span className="mode">● 模拟模式 / {(status?.backend || 'MOCK').toUpperCase()}</span></header>
    {error && <div className="error" role="alert">{error}</div>}
    <p role="status">事件连接：{connected ? '已连接' : '重连中'}{reply && ` · ${reply}`}</p>
    <section className="grid">
      <aside className="panel interaction"><h2>交互指令</h2><form onSubmit={(e) => { e.preventDefault(); send() }}><input value={text} onChange={(e) => setText(e.target.value)} placeholder="例如：去客厅" aria-label="指令" /><button>发送</button></form><div className="shortcuts">{['大头大头', '跟着我', '握手', '跟我开始记录场地'].map((item) => <button key={item} onClick={() => send(item)}>{item}</button>)}</div><div className="notice">所有通知均为模拟记录，不会联系真实家人。</div></aside>
      <section className="panel robot"><h2>机器人状态</h2><div className={`orb ${status?.stopped ? 'stopped' : 'active'}`}><div className="dog">🐕</div></div><strong>{status?.stopped ? '已停止' : '执行中'}</strong><p>{status?.model_label || '连接中…'}</p><dl><div><dt>场景</dt><dd>{status?.scene || '—'}</dd></div><div><dt>位置</dt><dd>{status?.position?.map((n) => n.toFixed(2)).join(', ') || '—'}</dd></div><div><dt>步态</dt><dd>{status?.locomotion ? '可用' : '尚不支持行走'}</dd></div></dl></section>
      <aside className="panel tasks"><h2>任务队列</h2>{tasks.length === 0 && <p className="muted">暂无任务</p>}{tasks.slice().reverse().map((task) => <article className="task" key={task.id}><div><b>{task.intent.destination || task.intent.intent}</b><small>{time(task.updated_at)}</small></div><span className={`status ${task.status}`}>{labels[task.status] || task.status}</span>{task.status === 'awaiting_confirmation' && <button className="confirm" onClick={() => run(() => api.confirm(task.id))}>确认联系家人</button>}{['queued', 'running'].includes(task.status) && <button className="cancel" onClick={() => run(() => api.cancel(task.id))}>取消</button>}{task.result?.delivery_status === 'simulated' && <small className="receipt">模拟通知已记录，不会实际发送</small>}{task.error_code && <small className="failure">{task.error_code}</small>}</article>)}</aside>
      <SimPanel />
    </section>
    <section className="bottom"><button className="stop" onClick={() => run(api.stop)}>紧急停止</button><select aria-label="恢复场景" value={scene} onChange={(e) => setScene(e.target.value)}><option value="living_room">客厅陪伴</option><option value="help">模拟求助</option><option value="obstacle">近障停止</option></select><button onClick={() => run(() => api.recover(scene))}>恢复模拟</button>{status?.backend === 'mock' && <button onClick={() => run(api.reset)}>重置演示</button>}<div className="timeline"><h2>事件时间线</h2>{events.filter((event) => event.type !== 'robot.state').slice(-8).reverse().map((event) => <div className="event" key={event.seq}><span>#{event.seq}</span><b>{event.type}</b>{typeof event.payload.code === 'string' && <span>{event.payload.code}</span>}<time>{time(event.timestamp)}</time></div>)}</div></section>
  </main>
}

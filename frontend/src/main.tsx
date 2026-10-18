import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Event, RobotStatus, Task } from './types'
import './style.css'

const labels: Record<string, string> = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消', timed_out: '超时', awaiting_confirmation: '待确认' }
const time = (value: string) => new Date(value).toLocaleTimeString()

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
      <aside className="panel interaction"><h2>交互指令</h2><form onSubmit={(e) => { e.preventDefault(); send() }}><input value={text} onChange={(e) => setText(e.target.value)} placeholder="例如：去客厅" aria-label="指令" /><button>发送</button></form><div className="shortcuts">{['帮我联系家人', '确认联系家人', '陪我去客厅', '去客厅', '你现在在哪儿'].map((item) => <button key={item} onClick={() => send(item)}>{item}</button>)}</div><div className="notice">所有通知均为模拟记录，不会联系真实家人。</div></aside>
      <section className="panel robot"><h2>机器人状态</h2><div className={`orb ${status?.stopped ? 'stopped' : 'active'}`}><div className="dog">🐕</div></div><strong>{status?.stopped ? '已停止' : '执行中'}</strong><p>{status?.model_label || '连接中…'}</p><dl><div><dt>场景</dt><dd>{status?.scene || '—'}</dd></div><div><dt>位置</dt><dd>{status?.position?.map((n) => n.toFixed(2)).join(', ') || '—'}</dd></div><div><dt>步态</dt><dd>{status?.locomotion ? '可用' : '尚不支持行走'}</dd></div></dl></section>
      <aside className="panel tasks"><h2>任务队列</h2>{tasks.length === 0 && <p className="muted">暂无任务</p>}{tasks.slice().reverse().map((task) => <article className="task" key={task.id}><div><b>{task.intent.destination || task.intent.intent}</b><small>{time(task.updated_at)}</small></div><span className={`status ${task.status}`}>{labels[task.status] || task.status}</span>{task.status === 'awaiting_confirmation' && <button className="confirm" onClick={() => run(() => api.confirm(task.id))}>确认联系家人</button>}{['queued', 'running'].includes(task.status) && <button className="cancel" onClick={() => run(() => api.cancel(task.id))}>取消</button>}{task.result?.delivery_status === 'simulated' && <small className="receipt">模拟通知已记录，不会实际发送</small>}{task.error_code && <small className="failure">{task.error_code}</small>}</article>)}</aside>
    </section>
    <section className="bottom"><button className="stop" onClick={() => run(api.stop)}>紧急停止</button><select aria-label="恢复场景" value={scene} onChange={(e) => setScene(e.target.value)}><option value="living_room">客厅陪伴</option><option value="help">模拟求助</option><option value="obstacle">近障停止</option></select><button onClick={() => run(() => api.recover(scene))}>恢复模拟</button>{status?.backend === 'mock' && <button onClick={() => run(api.reset)}>重置演示</button>}<div className="timeline"><h2>事件时间线</h2>{events.filter((event) => event.type !== 'robot.state').slice(-8).reverse().map((event) => <div className="event" key={event.seq}><span>#{event.seq}</span><b>{event.type}</b>{typeof event.payload.code === 'string' && <span>{event.payload.code}</span>}<time>{time(event.timestamp)}</time></div>)}</div></section>
  </main>
}

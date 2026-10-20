import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Event, RobotStatus, Task } from './types'
import './style.css'

const labels: Record<string, string> = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消', timed_out: '超时', awaiting_confirmation: '待确认' }
const time = (value: string) => new Date(value).toLocaleTimeString()

const SIM_URL = `http://${window.location.hostname}:8001`
interface SimState { person_state: string; seat_occupied: boolean; speaks: { t: number; text: string }[]; sim_time: number; dog_loaded: boolean; case_running: boolean; dog_state: string; seats: { id: string; occupied: boolean; by: string }[]; joints: number[]; viewer_running: boolean }
const personLabels: Record<string, string> = { sit: '坐着（座位占用）', stand: '站着（座位空）', away: '已离开（座位空）' }

interface VbotCheck { name: string; ok: boolean; detail: string }
interface VbotCfg { dog_ip: string; ssh_user: string; zenoh_endpoint: string; ros_domain_id: number; map_name: string; notes: string; owner_name?: string; dify_enabled?: boolean; dify_url?: string; dify_api_key?: string; dify_timeout?: number }
interface DifyTestResult { ok: boolean; error?: string; elapsed_ms?: number; sample?: string; intent?: { intent: string; confidence: number } }

function VbotPanel() {
  const [cfg, setCfg] = useState<VbotCfg | null>(null)
  const [checks, setChecks] = useState<VbotCheck[] | null>(null)
  const [found, setFound] = useState<string[] | null>(null)
  const [busy, setBusy] = useState('')
  const [difyTest, setDifyTest] = useState<DifyTestResult | null>(null)
  useEffect(() => { fetch('/api/vbot/config').then((r) => r.json()).then(setCfg).catch(() => setCfg(null)) }, [])
  const save = async () => { setBusy('save'); try { setCfg(await (await fetch('/api/vbot/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) })).json()); setChecks(null) } finally { setBusy('') } }
  const test = async () => { setBusy('test'); try { setChecks(await (await fetch('/api/vbot/test', { method: 'POST' })).json()) } catch (e) { setChecks([{ name: '自检请求失败', ok: false, detail: String(e) }]) } finally { setBusy('') } }
  const scan = async () => { setBusy('scan'); try { setFound(await (await fetch('/api/vbot/scan', { method: 'POST' })).json()) } catch (e) { setFound([]) } finally { setBusy('') } }
  const difyTestRun = async () => { setBusy('dify'); try { setDifyTest(await (await fetch('/api/vbot/dify-test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).json()) } catch (e) { setDifyTest({ ok: false, error: String(e) }) } finally { setBusy('') } }
  const set = (key: keyof VbotCfg) => (e: React.ChangeEvent<HTMLInputElement>) => setCfg(cfg ? { ...cfg, [key]: key === 'ros_domain_id' || key === 'dify_timeout' ? Number(e.target.value) : e.target.value } : cfg)
  return <aside className="panel vbot">
    <h2>真机连接配置</h2>
    {!cfg && <p className="muted">配置加载中…</p>}
    {cfg && <>
      <div className="cfg-grid">
        <label>狗 IP<input value={cfg.dog_ip} onChange={set('dog_ip')} /></label>
        <label>SSH 用户<input value={cfg.ssh_user} onChange={set('ssh_user')} /></label>
        <label>Zenoh<input value={cfg.zenoh_endpoint} onChange={set('zenoh_endpoint')} /></label>
        <label>DOMAIN<input value={cfg.ros_domain_id} onChange={set('ros_domain_id')} /></label>
        <label>地图名<input value={cfg.map_name} onChange={set('map_name')} placeholder="guide_site_1f" /></label>
        <span className="cfg-btns"><button onClick={save} disabled={!!busy}>{busy === 'save' ? '保存中…' : '保存'}</button><button onClick={test} disabled={!!busy}>{busy === 'test' ? '自检中…' : '自检'}</button><button onClick={scan} disabled={!!busy}>{busy === 'scan' ? '扫描中…' : '扫描找狗'}</button></span>
      </div>
      {found && <p className="notice">候选狗 IP（点击填入）：{found.length ? found.map((ip) => <button key={ip} onClick={() => setCfg(cfg ? { ...cfg, dog_ip: ip } : cfg)}>{ip}</button>) : '未发现——查网线/桥接/同网段'}</p>}
      {checks && <div className="checks">{checks.map((c) => <div key={c.name} title={c.detail}>{c.ok ? '✅' : '❌'} {c.name}</div>)}</div>}
      <h3 style={{ margin: '10px 0 4px' }}>Dify 意图识别（语音指令的大脑）</h3>
      <div className="cfg-grid">
        <label>主人称呼<input value={cfg.owner_name ?? ''} onChange={set('owner_name')} placeholder="流年" /></label>
        <label>Dify URL<input value={cfg.dify_url ?? ''} onChange={set('dify_url')} placeholder="http://127.0.0.1/v1/workflows/run" /></label>
        <label>API Key<input type="password" value={cfg.dify_api_key ?? ''} onChange={set('dify_api_key')} placeholder="app-…" /></label>
        <label>超时(秒)<input value={cfg.dify_timeout ?? 3} onChange={set('dify_timeout')} /></label>
        <label style={{ gridColumn: '1 / -1' }}><input type="checkbox" checked={cfg.dify_enabled ?? true} onChange={(e) => setCfg({ ...cfg, dify_enabled: e.target.checked })} /> 启用 Dify（关闭或超时自动降级本地规则，急停永远走本地）</label>
        <span className="cfg-btns"><button onClick={difyTestRun} disabled={!!busy}>{busy === 'dify' ? '测试中…' : '测试 Dify'}</button></span>
      </div>
      {difyTest && <p className="notice">{difyTest.ok ? `✅ ${difyTest.elapsed_ms}ms ·「${difyTest.sample}」→ 意图 ${difyTest.intent?.intent}（置信度 ${difyTest.intent?.confidence}）` : `❌ ${difyTest.error}`}</p>}
      <p className="notice">自检只读不碰控制；免密一次性：<code>ssh-copy-id {cfg.ssh_user}@{cfg.dog_ip}</code></p>
    </>}
  </aside>
}

const dogStateLabels: Record<string, string> = { idle: '待命', following: '跟随中', guiding: '引导中', crouch_watch: '蹲守空位' }

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
    {!sim && <p className="muted">仿真未启动：虚拟机上 bash scripts/start_all_vm.sh --viewer（3D 窗口看狗动态）</p>}
    {sim && <>
      <p role="status">狗：{dogStateLabels[sim.dog_state] || sim.dog_state} · 主人：{personLabels[sim.person_state] || sim.person_state}</p>
      <p role="status">座位：{(sim.seats || []).map((s) => `${s.id}${s.occupied ? `(${s.by})` : '(空)'}`).join(' ')}</p>
      {sim.dog_loaded && <p className="notice">狗关节(MuJoCo 真值 qpos 前6): {(sim.joints || []).join(', ')}</p>}
      <div className="shortcuts">
        <button onClick={act('/person/walk')}>主人走</button>
        <button onClick={act('/person/stand')}>主人停</button>
        <button onClick={act('/people/1/sit')}>路人1坐空位</button>
        <button onClick={act('/people/1/leave')}>路人1起身离开</button>
        <button onClick={act('/people/2/sit')}>路人2坐空位</button>
        <button onClick={act('/people/2/leave')}>路人2起身离开</button>
        <button onClick={act('/case/run')}>{sim.case_running ? '案例运行中…' : '跑最后十米案例'}</button>
        <button onClick={act('/viewer/open')}>开 3D 窗口</button>
        <button onClick={act('/viewer/stop')}>关 3D 窗口</button>
      </div>
      <p className="notice">3D 窗口={sim.viewer_running ? '已开（虚拟机桌面 MuJoCo 弹窗，活狗镜像）' : '未开'}；Wayland 会话下弹窗可能闪退，切 Xorg 会话可解</p>
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
  const [tab, setTab] = useState<'main' | 'config'>('main')
  const [page, setPage] = useState(0)
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
    <header><div><span className="eyebrow">GUARDIAN DOG</span><h1>最后十米守护者</h1></div><nav>{([['main', '主看板'], ['config', '连接配置']] as const).map(([key, label]) => <button key={key} className={tab === key ? 'mode' : ''} onClick={() => setTab(key)}>{label}</button>)}</nav><span className="mode">● 模拟模式 / {(status?.backend || 'MOCK').toUpperCase()}</span></header>
    {error && <div className="error" role="alert">{error}</div>}
    <p role="status">事件连接：{connected ? '已连接' : '重连中'}{reply && ` · ${reply}`}</p>
    {tab === 'main' && <>
    <section className="grid">
      <aside className="panel interaction"><h2>交互指令</h2><form onSubmit={(e) => { e.preventDefault(); send() }}><input value={text} onChange={(e) => setText(e.target.value)} placeholder="例如：检查链接状态" aria-label="指令" /><button>发送</button></form><div className="shortcuts">{['大头大头', '跟着我', '握手', '跟我开始记录场地', '检查链接状态'].map((item) => <button key={item} onClick={() => send(item)}>{item}</button>)}</div></aside>
      <section className="panel robot"><h2>机器人状态</h2><div className={`orb ${status?.stopped ? 'stopped' : 'active'}`}><div className="dog">🐕</div></div><strong>{status?.stopped ? '已停止' : '执行中'}</strong><p>{status?.model_label || '连接中…'}</p><dl><div><dt>场景</dt><dd>{status?.scene || '—'}</dd></div><div><dt>位置</dt><dd>{status?.position?.map((n) => n.toFixed(2)).join(', ') || '—'}</dd></div><div><dt>步态</dt><dd>{status?.locomotion ? '可用' : '尚不支持行走'}</dd></div></dl></section>
      <aside className="panel tasks"><h2>任务队列</h2>{tasks.length === 0 && <p className="muted">暂无任务</p>}{(() => { const ordered = tasks.slice().reverse(); const pages = Math.max(1, Math.ceil(ordered.length / 5)); const cur = Math.min(page, pages - 1); return <><div className="pager"><button aria-label="上一页" onClick={() => setPage(Math.max(0, cur - 1))}>‹ 上一页</button><span>第 {cur + 1}/{pages} 页</span><button aria-label="下一页" onClick={() => setPage(Math.min(pages - 1, cur + 1))}>下一页 ›</button></div>{ordered.slice(cur * 5, cur * 5 + 5).map((task) => <article className="task" key={task.id}><div><b>{task.intent.destination || task.intent.intent}</b><small>{time(task.updated_at)}</small></div><span className={`status ${task.status}`}>{labels[task.status] || task.status}</span>{task.status === 'awaiting_confirmation' && <button className="confirm" onClick={() => run(() => api.confirm(task.id))}>确认联系家人</button>}{['queued', 'running'].includes(task.status) && <button className="cancel" onClick={() => run(() => api.cancel(task.id))}>取消</button>}{task.result?.delivery_status === 'simulated' && <small className="receipt">模拟通知已记录，不会实际发送</small>}{task.error_code && <small className="failure">{task.error_code}</small>}{(task.result as { checks?: VbotCheck[]; passed?: number; total?: number } | null)?.checks && <small className="receipt" title={((task.result as { checks?: VbotCheck[] }).checks || []).map((c) => `${c.ok ? '✅' : '❌'} ${c.name}：${c.detail}`).join('\n')}>自检 {(task.result as { passed?: number }).passed}/{(task.result as { total?: number }).total} 通过（悬停看明细）</small>}</article>)}</>})()}</aside>
      <SimPanel />
    </section>
    <section className="bottom"><button className="stop" onClick={() => run(api.stop)}>紧急停止</button><button onClick={() => send('检查链接状态')}>检查链接状态</button><button onClick={() => run(() => api.recover(undefined))}>恢复模拟</button>{status?.backend === 'mock' && <button onClick={() => run(api.reset)}>重置演示</button>}<div className="timeline"><h2>事件时间线</h2>{events.filter((event) => event.type !== 'robot.state').slice(-8).reverse().map((event) => <div className="event" key={event.seq}><span>#{event.seq}</span><b>{event.type}</b>{typeof event.payload.code === 'string' && <span>{event.payload.code}</span>}<time>{time(event.timestamp)}</time></div>)}</div></section>
    </>}
    {tab === 'config' && <section className="grid"><VbotPanel /></section>}
  </main>
}

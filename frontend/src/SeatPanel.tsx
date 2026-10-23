import { useEffect, useState } from 'react'

interface Chair {
  id: number
  state: string
  direction: string
  score: number
  stable_frames: number
}
interface SeatStatus {
  status: string
  message: string
  error?: string
  chairs: Chair[]
  candidate_count: number
  age_seconds: number
  camera_stamp: number
}
const labels: Record<string, string> = {
  candidate: '疑似空位', person_overlap: '与人重叠', obstructed: '物品遮挡', uncertain: '无法判断',
}

export function SeatPanel() {
  const [status, setStatus] = useState<SeatStatus | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    let timer: number
    const poll = async () => {
      try {
        const response = await fetch('/api/seats/status', { signal: controller.signal })
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || '空位识别服务未连接')
        if (!controller.signal.aborted) { setStatus(data); setError('') }
      } catch (e) {
        if (!controller.signal.aborted) { setError(String(e instanceof Error ? e.message : e)); setStatus(null) }
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 1000)
      }
    }
    poll()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [])
  const find = async () => {
    setBusy(true); setNotice('')
    try {
      const response = await fetch('/api/seats/find', { method: 'POST' })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '提交失败')
      setNotice(data.message)
    } catch (e) { setNotice(String(e instanceof Error ? e.message : e)) }
    finally { setBusy(false) }
  }
  const online = status?.status === 'ok' && status.age_seconds <= 5
  return <section className="panel seats">
    <div className="seat-heading"><h2>真机找空位</h2><button disabled={!online || busy} onClick={find}>{busy ? '提交中…' : '找空位并播报'}</button></div>
    <p role="status">{error || status?.error || status?.message || '正在连接识别服务…'}</p>
    {notice && <p role="status" className="muted">{notice}</p>}
    {online && <>
      <img className="seat-camera" src={`/api/seats/frame?t=${status.camera_stamp}`} alt="机器狗摄像头与椅子识别标注" />
      <p className="muted">疑似空位 {status.candidate_count} 个 · 画面距今 {status.age_seconds.toFixed(1)} 秒</p>
      <ul className="seat-list">{status.chairs.map(chair => <li key={chair.id}>
        <b>椅子 {chair.id}</b> · {chair.direction} · {chair.state === 'candidate' && chair.stable_frames < 5 ? '连续确认中' : labels[chair.state] || chair.state} · 置信度 {Math.round(chair.score * 100)}%
      </li>)}</ul>
      {!status.chairs.length && <p className="muted">未检测到椅子，请让椅子完整进入镜头。</p>}
    </>}
    <p className="seat-hint">绿色为稳定的疑似空位，红色为与人重叠，黄色为遮挡或无法判断。仅提供画面方位，坐下前请现场确认座面。</p>
  </section>
}

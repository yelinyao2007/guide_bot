export type RobotStatus = {
  backend: string; model_label: string; available: boolean; stopped: boolean; locomotion: boolean;
  position: number[]; error_code: string | null; scene: string; simulation_time: number; worker_pid?: number | null
}
export type Task = {
  id: string; status: string; intent: { intent: string; destination?: string | null; reply?: string };
  created_at: string; updated_at: string; error_code?: string | null; result?: Record<string, unknown> | null
}
export type Event = { seq: number; type: string; timestamp: string; payload: Record<string, unknown> }

import { http, TOKEN_KEY } from './http'
import type { EntityId, PaginatedResponse } from '@/types'

export interface AgentReference {
  id: EntityId
  name: string
  content_type: string
  version: number
}
export interface AgentArticle {
  title: string
  excerpt: string
  content: string
}
export interface AgentTurn {
  id: string
  conversation_id: string
  request_id: string
  sequence: number
  content: string
  skill_snapshot: AgentReference[]
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  attempts: number
  reply: string | null
  article: AgentArticle | null
  error: string | null
  article_id: string | null
  created_at: string
  updated_at: string
}
export interface AgentConversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}
export interface AgentConversationDetail extends AgentConversation {
  turns: AgentTurn[]
}
export interface AgentMessagePayload {
  request_id: string
  content: string
  skill_ids: EntityId[]
}
export type AgentStreamEvent =
  | { type: 'status'; phase: string; label: string; status?: string; turn_id?: string }
  | { type: 'reasoning'; delta: string; turn_id?: string }
  | { type: 'output'; delta: string; turn_id?: string }
  | {
      type: 'tool'
      phase: 'started' | 'arguments'
      name?: string
      call_id?: string
      delta?: string
      turn_id?: string
    }
  | { type: 'done'; status: AgentTurn['status']; turn_id?: string }
  | { type: 'deleted'; turn_id?: string }
  | { type: 'error'; phase: 'failed'; message: string; turn_id?: string }

type StreamListener = (event: AgentStreamEvent) => void
const base = '/agent-chat/conversations'
const apiBase = String(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')
export const agentChatApi = {
  async list(page = 1) {
    return (
      await http.get<PaginatedResponse<AgentConversation>>(base, {
        params: { page, page_size: 30 },
      })
    ).data
  },
  async create() {
    return (await http.post<AgentConversation>(base)).data
  },
  async detail(id: string) {
    return (await http.get<AgentConversationDetail>(`${base}/${id}`)).data
  },
  async remove(id: string) {
    await http.delete(`${base}/${encodeURIComponent(id)}`)
  },
  async send(id: string, payload: AgentMessagePayload) {
    return (await http.post<AgentTurn>(`${base}/${id}/messages`, payload)).data
  },
  async stream(id: string, turnId: string, signal: AbortSignal, onEvent: StreamListener) {
    const token = localStorage.getItem(TOKEN_KEY)
    const response = await fetch(
      `${apiBase}${base}/${encodeURIComponent(id)}/messages/${encodeURIComponent(turnId)}/stream`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {}, signal },
    )
    if (response.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem('x-sentinel-user')
      window.location.assign(`/login?redirect=${encodeURIComponent(window.location.pathname)}`)
      throw new Error('登录状态已失效')
    }
    if (!response.ok) {
      const error = await response.json().catch(() => null)
      throw new Error(error?.detail || `对话流连接失败（HTTP ${response.status}）`)
    }
    if (!response.body) throw new Error('当前浏览器不支持对话流')
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
        const frames = buffer.split('\n\n')
        buffer = frames.pop() || ''
        for (const frame of frames) {
          if (!frame || frame.startsWith(':')) continue
          const data: string[] = []
          for (const line of frame.split('\n')) {
            if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
          }
          if (!data.length) continue
          const event = JSON.parse(data.join('\n')) as AgentStreamEvent
          onEvent(event)
          if (event.type === 'done' || event.type === 'error' || event.type === 'deleted') return
        }
      }
    } finally {
      await reader.cancel().catch(() => undefined)
      reader.releaseLock()
    }
  },
  async cancel(id: string, turn: string) {
    return (await http.post<AgentTurn>(`${base}/${id}/messages/${turn}/cancel`)).data
  },
  async retry(id: string, turn: string) {
    return (await http.post<AgentTurn>(`${base}/${id}/messages/${turn}/retry`)).data
  },
  async save(id: string, turn: string) {
    return (await http.post<{ article_id: string }>(`${base}/${id}/messages/${turn}/article`)).data
  },
}

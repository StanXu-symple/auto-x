import { http } from './http'
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
const base = '/agent-chat/conversations'
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
  async send(id: string, payload: AgentMessagePayload) {
    return (await http.post<AgentTurn>(`${base}/${id}/messages`, payload)).data
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

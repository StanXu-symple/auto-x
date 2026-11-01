import { http } from './http'
import type { AiSkill, EntityId, PaginatedResponse } from '@/types'

export type NodeType = 'custom' | 'article' | 'review'
export interface AgentNodeInput {
  name: string
  description: string | null
  node_type: NodeType
  skill_ids: EntityId[]
  is_active: boolean
}
export interface AgentNode extends AgentNodeInput {
  id: number
  version: number
  created_at: string
  updated_at: string
}
export interface AgentWorkflowInput {
  name: string
  description: string | null
  node_ids: number[]
  is_active: boolean
}
export interface AgentWorkflow extends AgentWorkflowInput {
  id: number
  version: number
  nodes: AgentNode[]
  created_at: string
  updated_at: string
}
export interface WorkflowRun {
  id: string
  workflow_id: number | null
  workflow_name: string
  workflow_version: number
  status: string
  current_position: number
  error: string | null
  article_id: EntityId | null
  created_at: string
  updated_at: string
  completed_at: string | null
}
export interface WorkflowOutput {
  text?: string
  title?: string
  content?: string
  excerpt?: string | null
  score?: number
  decision?: 'pass' | 'revise' | 'reject'
  summary?: string
  issues?: string[]
  suggestions?: string[]
}
export interface WorkflowRunDetail extends WorkflowRun {
  input_text: string
  source_snapshot: Record<string, unknown> | null
  source_changed: boolean
  workflow_snapshot: { steps: Array<{ name: string; version: number; skills: AiSkill[] }> }
  config_snapshot: { model: string; data_source_name: string }
  steps: Array<{
    position: number
    node_name: string
    node_type: NodeType
    status: string
    attempts: number
    output: WorkflowOutput | null
    usage: { input_tokens?: number; output_tokens?: number } | null
    error: string | null
    started_at: string | null
    completed_at: string | null
  }>
}
export interface WorkflowRunInput {
  workflow_id: number
  workflow_version: number
  request_id: string
  input_text: string
  article_id?: EntityId
  article_revision?: number
  tweet_id?: string
}
const nodesPath = '/ai/agent-nodes'
const flowsPath = '/ai/agent-workflows'
const runsPath = '/ai/agent-workflow-runs'
export const agentWorkflowsApi = {
  async nodes(params: { page?: number; page_size?: number; active?: boolean } = {}) {
    return (await http.get<PaginatedResponse<AgentNode>>(nodesPath, { params })).data
  },
  async saveNode(payload: AgentNodeInput, existing?: AgentNode | null) {
    return existing
      ? (
          await http.patch<AgentNode>(`${nodesPath}/${existing.id}`, {
            ...payload,
            version: existing.version,
          })
        ).data
      : (await http.post<AgentNode>(nodesPath, payload)).data
  },
  async disableNode(id: number) {
    await http.delete(`${nodesPath}/${id}`)
  },
  async flows(params: { page?: number; page_size?: number; active?: boolean } = {}) {
    return (await http.get<PaginatedResponse<AgentWorkflow>>(flowsPath, { params })).data
  },
  async flow(id: number) {
    return (await http.get<AgentWorkflow>(`${flowsPath}/${id}`)).data
  },
  async saveFlow(payload: AgentWorkflowInput, existing?: AgentWorkflow | null) {
    return existing
      ? (
          await http.put<AgentWorkflow>(`${flowsPath}/${existing.id}`, {
            ...payload,
            version: existing.version,
          })
        ).data
      : (await http.post<AgentWorkflow>(flowsPath, payload)).data
  },
  async disableFlow(id: number) {
    await http.delete(`${flowsPath}/${id}`)
  },
  async run(payload: WorkflowRunInput) {
    return (await http.post<WorkflowRun>(runsPath, payload)).data
  },
  async runs(
    params: { page?: number; page_size?: number; workflow_id?: number; article_id?: EntityId } = {},
  ) {
    return (await http.get<PaginatedResponse<WorkflowRun>>(runsPath, { params })).data
  },
  async detail(id: string) {
    return (await http.get<WorkflowRunDetail>(`${runsPath}/${id}`)).data
  },
  async cancel(id: string) {
    await http.post(`${runsPath}/${id}/cancel`)
  },
  async retry(id: string) {
    await http.post(`${runsPath}/${id}/retry`)
  },
  async saveArticle(id: string) {
    return (await http.post<{ article_id: EntityId }>(`${runsPath}/${id}/article`)).data
  },
}
export async function allPages<T>(
  fetcher: (query: { page: number; page_size: number }) => Promise<PaginatedResponse<T>>,
) {
  const items: T[] = []
  for (let page = 1; ; page++) {
    const result = await fetcher({ page, page_size: 100 })
    items.push(...result.items)
    if (!result.items.length || items.length >= result.total) return items
  }
}
export const nodeTypeOptions = [
  { value: 'custom', label: '文本处理', description: '分析、提纲、总结等，输出文字结果' },
  {
    value: 'article',
    label: '文章生成 / 修订',
    description: '输出标题、正文和摘要，可保存到文章管理',
  },
  { value: 'review', label: '文章审核 / 评分', description: '输出分数、通过与否、问题和修改建议' },
]
export const nodeTypeLabel = (value: string) =>
  nodeTypeOptions.find((item) => item.value === value)?.label || value
export const runStatusLabel = (value: string) =>
  ({
    pending: '等待前序节点',
    queued: '排队中',
    running: '执行中',
    retry_wait: '等待重试',
    succeeded: '执行完成',
    failed: '执行失败',
    cancelled: '已取消',
  })[value] || value
export const runActive = (value: string) => ['queued', 'running', 'retry_wait'].includes(value)

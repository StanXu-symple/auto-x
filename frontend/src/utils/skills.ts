import type { AiSkill } from '@/types'

export const skillSourceLabel = (skill: Pick<AiSkill, 'source_type'>) =>
  skill.source_type === 'github' ? 'GitHub' : '自定义'

export const skillOptionLabel = (skill: AiSkill) =>
  `${skill.name} · ${skillSourceLabel(skill)}${skill.is_active ? '' : '（已停用）'}`

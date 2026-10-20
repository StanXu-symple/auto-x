import type { AiSkill } from '@/types'

export const skillSourceLabel = (skill: Pick<AiSkill, 'source_type'>) =>
  skill.source_type === 'github' ? 'GitHub' : '自定义'

export const skillOptionLabel = (skill: AiSkill) =>
  `${skill.name} · ${skillSourceLabel(skill)}${skill.is_active ? '' : '（已停用）'}`

export interface SkillTableRow extends AiSkill {
  key: string
  kind: 'group' | 'skill'
  children?: SkillTableRow[]
  activeCount?: number
}

// Match the saved import scope used by update-github, including legacy packages.
export function groupSkills(skills: AiSkill[]): SkillTableRow[] {
  const rows: SkillTableRow[] = []
  const groups = new Map<string, SkillTableRow>()
  for (const skill of skills) {
    const child: SkillTableRow = { ...skill, key: `skill:${skill.id}`, kind: 'skill' }
    const source = skill.source_metadata
    if (skill.source_type !== 'github' || !source) {
      rows.push(child)
      continue
    }
    const path = source.import_path ?? source.path
    const key = `github:${JSON.stringify([source.repository, source.ref, path])}`
    let group = groups.get(key)
    if (!group) {
      group = {
        ...skill,
        key,
        kind: 'group',
        name: source.repository.split('/').pop() || source.repository,
        description: `${path || '仓库根目录'} · ${source.ref}`,
        children: [],
        activeCount: 0,
      }
      groups.set(key, group)
      rows.push(group)
    }
    group.children!.push(child)
    if (skill.is_active) group.activeCount!++
  }
  return rows
}

export function skillSourceSummary(skill: AiSkill): string {
  const source = skill.source_metadata
  if (skill.source_type !== 'github' || !source) return skillSourceLabel(skill)
  return `GitHub · ${source.repository}`
}

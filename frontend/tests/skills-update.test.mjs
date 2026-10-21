import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, ref } from 'vue'
import ts from 'typescript'

const tmpRoot = new URL('../node_modules/.tmp/', import.meta.url)
mkdirSync(tmpRoot, { recursive: true })
const directory = mkdtempSync(fileURLToPath(new URL('skills-update-', tmpRoot)))
const compiledPath = `${directory}/SkillsView.mjs`
const utilsPath = `${directory}/skills.mjs`
writeFileSync(
  utilsPath,
  ts.transpileModule(readFileSync(new URL('../src/utils/skills.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText,
)
const { groupSkills } = await import(pathToFileURL(utilsPath).href)
const source = readFileSync(new URL('../src/views/SkillsView.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = ts.transpileModule(
  compileScript(descriptor, { id: 'skills-update-test' }).content,
  {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  },
).outputText

function mockImport(line, module) {
  if (module === 'vue') return line
  if (module === '@/services/api')
    return `const aiApi = new Proxy({}, { get: (_target, method) => (...args) => globalThis.skillsUpdateMocks.api[method](...args) });`
  if (module === '@/services/http')
    return 'const getErrorMessage = (error, fallback) => error?.message || fallback;'
  if (module === '@/utils/skills') return line.replace('@/utils/skills', './skills.mjs')
  if (module === 'ant-design-vue')
    return `const message = new Proxy({}, { get: (_target, method) => (...args) => globalThis.skillsUpdateMocks.messages.push([method, ...args]) });
      const Modal = { confirm() {} };`
  if (module === '@ant-design/icons-vue') {
    const names = line.match(/^import\s+\{([^}]+)\}/)?.[1]
    assert.ok(names, 'expected named icon imports')
    return names
      .split(',')
      .map((name) => name.trim())
      .filter(Boolean)
      .map((name) => `const ${name} = null;`)
      .join('\n')
  }
  if (module.endsWith('.vue')) {
    const name = line.match(/^import\s+([\w$]+)\s+from/)?.[1]
    assert.ok(name, `expected a default component import for ${module}`)
    return `const ${name} = null;`
  }
  throw new Error(`Unexpected SkillsView import: ${module}`)
}

writeFileSync(
  compiledPath,
  compiled.replace(/^import\s+.+?\s+from\s+['"]([^'"]+)['"];?$/gm, (line, module) =>
    mockImport(line, module),
  ),
)
const { default: SkillsView } = await import(pathToFileURL(compiledPath).href)
// Exercise the real view setup without rendering Ant Design or making network requests.
SkillsView.render = () => null
const renderer = createRenderer({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert() {},
  remove() {},
  setText() {},
  setElementText() {},
  patchProp() {},
  parentNode: () => null,
  nextSibling: () => null,
})

after(() => {
  delete globalThis.skillsUpdateMocks
  rmSync(directory, { recursive: true, force: true })
})

function setup(t, updateGithubSkill, fetchPage = async () => ({ items: [], total: 0 })) {
  const calls = []
  const messages = []
  let loads = 0
  globalThis.skillsUpdateMocks = {
    api: {
      updateGithubSkill: async (...args) => {
        calls.push(args)
        return updateGithubSkill(...args)
      },
      skillPage: async (...args) => {
        loads++
        return fetchPage(...args)
      },
    },
    messages,
  }
  const instance = ref()
  const app = renderer.createApp({ render: () => h(SkillsView, { ref: instance }) })
  app.mount({})
  t.after(() => app.unmount())
  return {
    state: instance.value.$.setupState,
    calls,
    messages,
    get loads() {
      return loads
    },
  }
}

const skill = {
  id: 42,
  name: 'Writer',
  source_type: 'github',
  source_metadata: {
    repository: 'owner/repo',
    ref: 'main',
    import_path: 'skills',
    path: 'skills/writer',
  },
}
const result = {
  repository: 'owner/repo',
  commit: 'a'.repeat(40),
  created: 0,
  updated: 1,
  unchanged: 0,
  skills: [skill],
  warnings: [],
}

test('update sends the saved Skill id and prevents duplicate requests until it finishes', async (t) => {
  let finish
  const pending = new Promise((resolve) => {
    finish = resolve
  })
  const view = setup(t, () => pending)
  const updating = view.state.updateGithub(skill)
  assert.equal(view.state.importOpen, true)
  assert.equal(view.state.importing, true)
  assert.equal(view.state.updatingSkill.id, 42)
  await view.state.updateGithub({ ...skill, id: 43 })
  view.state.openImport()
  assert.equal(view.state.updatingSkill.id, 42)
  assert.deepEqual(view.calls, [[42]])
  finish(result)
  await updating
  assert.equal(view.state.importing, false)
  assert.deepEqual(JSON.parse(JSON.stringify(view.state.importResult)), result)
  assert.equal(view.loads, 2)
  assert.deepEqual(view.messages.at(-1), ['success', 'GitHub Skills 已更新'])
})

test('unchanged updates report latest version and custom Skills cannot request a Git update', async (t) => {
  const view = setup(t, async () => ({ ...result, updated: 0, unchanged: 1 }))
  await view.state.updateGithub({ ...skill, source_type: 'custom' })
  assert.equal(view.calls.length, 0)
  await view.state.updateGithub(skill)
  assert.deepEqual(view.messages.at(-1), ['success', '已是最新版本'])
  view.state.openImport()
  assert.equal(view.state.updatingSkill, null)
  assert.equal(view.state.importResult, null)
  assert.deepEqual(JSON.parse(JSON.stringify(view.state.importForm)), {
    url: '',
    ref: '',
    path: '',
  })
})

test('failed update keeps the result dialog open and retries by id without editing the source', async (t) => {
  let attempt = 0
  const view = setup(t, async () => {
    if (++attempt === 1) throw new Error('仓库暂不可用')
    return result
  })
  await view.state.updateGithub(skill)
  assert.equal(view.state.importError, '仓库暂不可用')
  assert.equal(view.state.importOpen, true)
  assert.equal(view.state.importing, false)
  assert.equal(view.state.importResult, null)
  await view.state.importGithub()
  assert.deepEqual(view.calls, [[42], [42]])
  assert.equal(view.state.importError, '')
  assert.equal(view.state.importResult.updated, 1)
})

function githubSkill(id, metadata = {}, active = true) {
  return {
    ...skill,
    id,
    name: `Skill ${id}`,
    is_active: active,
    source_metadata: { ...skill.source_metadata, path: `skills/skill-${id}`, ...metadata },
  }
}

test('groups by saved repository, ref and import scope while custom Skills remain independent', () => {
  const first = githubSkill(1, { commit: 'a'.repeat(40) })
  const second = githubSkill(2, { commit: 'b'.repeat(40) }, false)
  const input = [
    first,
    { id: 3, name: 'Custom', source_type: 'custom', is_active: true },
    second,
    githubSkill(4, { import_path: 'other-skills' }),
    githubSkill(5, { ref: 'feature/skills' }),
    githubSkill(6, { repository: 'another/repo' }),
  ]
  const rows = groupSkills(input)
  assert.equal(rows.length, 5)
  assert.deepEqual(
    rows[0].children.map((row) => row.id),
    [1, 2],
  )
  assert.equal(rows[0].activeCount, 1)
  assert.equal(rows[0].id, first.id, 'a group retains a real Skill id for the update API')
  assert.equal(rows[1].kind, 'skill')
  assert.equal(rows[1].children, undefined)
  assert.equal(new Set(rows.map((row) => row.key)).size, 5)
  assert.equal(input[0].children, undefined, 'grouping does not mutate API records')
})

test('root imports and legacy per-Skill scopes match the backend update scope', () => {
  const rows = groupSkills([
    githubSkill(1, { import_path: '' }),
    githubSkill(2, { import_path: '' }),
    githubSkill(3, { import_path: undefined }),
    githubSkill(4, { import_path: undefined }),
  ])
  assert.equal(rows.length, 3)
  assert.deepEqual(
    rows[0].children.map((row) => row.id),
    [1, 2],
  )
  assert.match(rows[0].description, /仓库根目录/)
  assert.notEqual(rows[1].key, rows[2].key)
})

test('loads all API pages before grouping and paginates complete parent rows', async (t) => {
  const items = Array.from({ length: 105 }, (_, index) => githubSkill(index + 1))
  const pages = []
  const view = setup(
    t,
    async () => result,
    async ({ page, page_size }) => {
      pages.push(page)
      return { items: items.slice((page - 1) * page_size, page * page_size), total: items.length }
    },
  )
  await view.state.loadSkills()
  assert.equal(pages.includes(2), true)
  assert.equal(view.state.skills.length, 105)
  assert.equal(view.state.skillRows.length, 1)
  assert.equal(view.state.skillRows[0].children.length, 105)
  assert.equal(view.state.skillsPagination.total, 1)
  assert.deepEqual(view.state.expandedRowKeys, [])
  view.state.toggleGroup(view.state.skillRows[0].key)
  const keys = [...view.state.expandedRowKeys]
  await view.state.loadSkills()
  assert.deepEqual(view.state.expandedRowKeys, keys)
  await view.state.updateGithub(view.state.skillRows[0])
  assert.deepEqual(view.calls, [[1]])
  assert.deepEqual(view.state.expandedRowKeys, keys)
})

test('refresh clamps the last parent page and removes expansion for deleted groups', async (t) => {
  let items = Array.from({ length: 16 }, (_, index) =>
    githubSkill(index + 1, { import_path: `group-${index}` }),
  )
  const view = setup(
    t,
    async () => result,
    async () => ({ items, total: items.length }),
  )
  await view.state.loadSkills()
  view.state.changeSkillPage({ current: 2, pageSize: 15 })
  view.state.toggleGroup(view.state.skillRows[15].key)
  items = items.slice(0, 15)
  await view.state.loadSkills()
  assert.equal(view.state.skillsPagination.current, 1)
  assert.equal(view.state.skillsPagination.total, 15)
  assert.deepEqual(view.state.expandedRowKeys, [])
})

test('a failed later API page preserves the previous complete list', async (t) => {
  let fail = false
  const view = setup(
    t,
    async () => result,
    async ({ page }) => {
      if (!fail) return { items: [githubSkill(1)], total: 1 }
      if (page === 2) throw new Error('第二页加载失败')
      return { items: [githubSkill(2)], total: 2 }
    },
  )
  await view.state.loadSkills()
  fail = true
  await view.state.loadSkills()
  assert.deepEqual(
    view.state.skills.map((row) => row.id),
    [1],
  )
  assert.equal(view.state.skillsLoading, false)
  assert.deepEqual(view.messages.at(-1), ['error', '第二页加载失败'])
})

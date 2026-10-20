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
  if (module === '@/composables/usePagedTable')
    return 'const usePagedTable = (...args) => globalThis.skillsUpdateMocks.usePagedTable(...args);'
  if (module === '@/services/api')
    return `const aiApi = new Proxy({}, { get: (_target, method) => (...args) => globalThis.skillsUpdateMocks.api[method](...args) });
      const monitoredUsersApi = { list: async () => ({ items: [] }) };`
  if (module === '@/services/http')
    return 'const getErrorMessage = (error, fallback) => error?.message || fallback;'
  if (module === '@/utils/skills')
    return 'const skillOptionLabel = () => ""; const skillSourceLabel = () => "";'
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

function setup(t, updateGithubSkill) {
  const calls = []
  const messages = []
  let loads = 0
  globalThis.skillsUpdateMocks = {
    api: {
      updateGithubSkill: async (...args) => {
        calls.push(args)
        return updateGithubSkill(...args)
      },
      skillPage: async () => ({ items: [], total: 0 }),
    },
    messages,
    usePagedTable: () => ({
      rows: ref([]), total: ref(0), loading: ref(false), pagination: ref({}),
      load: async () => { loads++ }, change: async () => {},
    }),
  }
  const instance = ref()
  const app = renderer.createApp({ render: () => h(SkillsView, { ref: instance }) })
  app.mount({})
  t.after(() => app.unmount())
  return {
    state: instance.value.$.setupState, calls, messages,
    get loads() { return loads },
  }
}

const skill = {
  id: 42, name: 'Writer', source_type: 'github',
  source_metadata: { repository: 'owner/repo', ref: 'main', import_path: 'skills', path: 'skills/writer' },
}
const result = { repository: 'owner/repo', commit: 'a'.repeat(40), created: 0, updated: 1, unchanged: 0, skills: [skill], warnings: [] }

test('update sends the saved Skill id and prevents duplicate requests until it finishes', async (t) => {
  let finish
  const pending = new Promise((resolve) => { finish = resolve })
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
  assert.deepEqual(JSON.parse(JSON.stringify(view.state.importForm)), { url: '', ref: '', path: '' })
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

import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'
import ts from 'typescript'

const tmpRoot = new URL('../node_modules/.tmp/', import.meta.url)
mkdirSync(tmpRoot, { recursive: true })
const directory = mkdtempSync(fileURLToPath(new URL('selected-skill-details-', tmpRoot)))
writeFileSync(
  `${directory}/skills.mjs`,
  ts.transpileModule(readFileSync(new URL('../src/utils/skills.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText,
)

async function loadComponent(relativePath, name) {
  const { descriptor } = parse(readFileSync(new URL(relativePath, import.meta.url), 'utf8'))
  const compiled = ts.transpileModule(
    compileScript(descriptor, { id: `selected-skill-details-${name}` }).content,
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } },
  ).outputText
  const path = `${directory}/${name}.mjs`
  writeFileSync(
    path,
    compiled.replace(/^import\s+.+?\s+from\s+['"]([^'"]+)['"];?$/gm, (line, module) => {
      if (module === 'vue') return line
      if (module === 'vue-router') return 'const useRouter = () => ({ push() {} });'
      if (module === '@/services/api')
        return line
          .match(/^import\s+\{([^}]+)\}/)[1]
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean)
          .map(
            (api) =>
              `const ${api} = new Proxy({}, { get: (_target, method) => (...args) => globalThis.selectedSkillDetailsMocks.call(${JSON.stringify(api)}, method, args) });`,
          )
          .join('\n')
      if (module === '@/services/http')
        return 'const getErrorMessage = (error, fallback) => error?.message || fallback;'
      if (module === '@/utils/skills') return line.replace('@/utils/skills', './skills.mjs')
      if (module === '@/utils/format')
        return 'const formatDateTime = () => ""; const formatDuration = () => ""; const formatNumber = () => ""; const formatRelative = () => ""; const tweetTime = () => "";'
      if (module === '@/composables/usePagedTable')
        return 'const usePagedTable = () => ({ rows: ref([]), total: ref(0), loading: ref(false), pagination: ref({}), load: async () => {}, reset: async () => {}, change() {} });'
      if (module === 'ant-design-vue')
        return 'const message = { warning() {}, error() {}, success() {} }; const Modal = { confirm() {} };'
      if (module === '@ant-design/icons-vue')
        return line
          .match(/^import\s+\{([^}]+)\}/)[1]
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean)
          .map((icon) => `const ${icon.split(/\s+as\s+/).at(-1)} = null;`)
          .join('\n')
      if (module.endsWith('.vue'))
        return `const ${line.match(/^import\s+([\w$]+)\s+from/)[1]} = null;`
      throw new Error(`Unexpected ${name} import: ${module}`)
    }),
  )
  const { default: component } = await import(pathToFileURL(path).href)
  component.render = () => null
  return component
}

const TweetsView = await loadComponent('../src/views/TweetsView.vue', 'TweetsView')
const AiListenTasks = await loadComponent('../src/components/AiListenTasks.vue', 'AiListenTasks')
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
  delete globalThis.selectedSkillDetailsMocks
  rmSync(directory, { recursive: true, force: true })
})

async function setup(t, component) {
  const calls = []
  globalThis.selectedSkillDetailsMocks = {
    call: async (api, method, args) => {
      calls.push([api, method, ...args])
      if (method === 'list' || method === 'skillPage') return { items: [], total: 0 }
      return {}
    },
  }
  const instance = ref()
  const app = renderer.createApp({ render: () => h(component, { ref: instance }) })
  app.mount({})
  t.after(() => app.unmount())
  await new Promise((resolve) => setImmediate(resolve))
  return { state: instance.value.$.setupState, calls }
}

const custom = {
  id: 11,
  name: '自定义写作',
  instructions: '保留第一段。\n第二段保持原始格式。',
  source_type: 'custom',
  is_active: true,
}
const github = {
  id: '22',
  name: 'GitHub 写作',
  instructions: '# 写作规则\n\n完整仓库提示词。\n\n## 参考\n继续正文。',
  source_type: 'github',
  source_metadata: { repository: 'owner/repo', path: 'skills/writer' },
  is_active: true,
}

test('tweet generation opens each selected Skill with full prompts and preserves generation order', async (t) => {
  const { state, calls } = await setup(t, TweetsView)
  state.generateOpen = true
  state.generateTweet = { id: 1, tweet_id: 'tweet-1' }
  state.generationSkills = [custom, github]
  state.selectedSkillIds = [22, '11']
  assert.deepEqual(
    state.selectedGenerationSkills.map(({ skill }) => skill.name),
    [github.name, custom.name],
  )
  for (const { skill } of state.selectedGenerationSkills) {
    state.openSkillDetails(skill)
    assert.equal(state.skillDetailsOpen, true)
    assert.equal(state.detailSkill.instructions, skill.instructions)
  }
  assert.deepEqual(state.selectedSkillIds, [22, '11'])
  await state.generate()
  const generation = calls.find(
    ([api, method]) => api === 'aiApi' && method === 'generateFromTweet',
  )
  assert.equal(generation[2], 'tweet-1')
  assert.deepEqual(generation[3].skill_ids, [22, '11'])
  await nextTick()
  assert.equal(state.skillDetailsOpen, false)
})

test('missing selected tweet Skills keep their identity and cannot open details', async (t) => {
  const { state } = await setup(t, TweetsView)
  state.selectedSkillIds = [99]
  assert.deepEqual(state.selectedGenerationSkills, [{ id: 99, skill: undefined }])
  state.openSkillDetails(state.selectedGenerationSkills[0].skill)
  assert.equal(state.detailSkill, null)
  assert.equal(state.skillDetailsOpen, false)
})

test('new and edited listening tasks expose selected prompts without changing their payload', async (t) => {
  const { state } = await setup(t, AiListenTasks)
  state.skills = [custom, github]
  for (const task of [undefined, { id: 7, name: '原监听任务', skill_ids: ['22', 11] }]) {
    state.setForm(task)
    await nextTick()
    if (!task) state.form.skill_ids = [11, '22']
    const original = JSON.parse(JSON.stringify(state.payload()))
    assert.deepEqual(
      state.selectedFormSkills.map(({ id }) => id),
      original.skill_ids,
    )
    for (const { skill } of state.selectedFormSkills) {
      state.openSkillDetails(skill)
      assert.equal(state.skillDetailsOpen, true)
      assert.equal(state.detailSkill.instructions, skill.instructions)
    }
    assert.deepEqual(state.payload(), original)
    state.editorOpen = false
    await nextTick()
    assert.equal(state.skillDetailsOpen, false)
  }
})

test('deleted listening task Skills remain visible as unavailable and cannot open details', async (t) => {
  const { state } = await setup(t, AiListenTasks)
  state.setForm({ id: 7, name: '已删除 Skill 的任务', skill_ids: [99] })
  assert.deepEqual(state.selectedFormSkills, [{ id: 99, skill: undefined }])
  state.openSkillDetails(state.selectedFormSkills[0].skill)
  assert.equal(state.detailSkill, null)
  assert.equal(state.skillDetailsOpen, false)
  assert.deepEqual(state.payload().skill_ids, [99])
})

for (const [name, component] of [
  ['tweets', TweetsView],
  ['listening', AiListenTasks],
]) {
  test(`${name} split selectors preserve interleaved order and unknown IDs`, async (t) => {
    const { state } = await setup(t, component)
    const resources = [custom, github, { ...custom, id: 33, content_type: 'prompt' }]
    if (name === 'tweets') {
      state.generationSkills = resources
      state.selectedSkillIds = ['11', 22, 99]
    } else {
      state.skills = resources
      state.setForm({ id: 7, name: 'Old task', skill_ids: ['11', 22, 99] })
    }
    const ids = () => (name === 'tweets' ? state.selectedSkillIds : state.payload().skill_ids)
    assert.deepEqual(state.selectedPromptIds, ['11'])
    assert.deepEqual(state.selectedGenerationSkillIds, [22])
    state.selectedPromptIds = [33, 11, '33', 22]
    assert.deepEqual(ids(), ['11', 22, 99, 33], 'deduplicate IDs and reject the other category')
    state.selectedGenerationSkillIds = []
    assert.deepEqual(ids(), ['11', 99, 33])
    state.selectedPromptIds = [33]
    assert.deepEqual(ids(), [99, 33], 'unavailable historical IDs are not silently removed')
  })

  test(`${name} combined selection limit spans Skills and prompts`, async (t) => {
    const { state } = await setup(t, component)
    const resources = Array.from({ length: 22 }, (_, index) => ({
      ...custom,
      id: index + 1,
      content_type: index % 2 ? 'skill' : 'prompt',
    }))
    if (name === 'tweets') {
      state.generationSkills = resources
      state.selectedSkillIds = resources.slice(0, 20).map(({ id }) => id)
    } else {
      state.skills = resources
      state.form.skill_ids = resources.slice(0, 20).map(({ id }) => id)
    }
    const allOptions = () => [
      ...state.generationSkillOptions,
      ...(name === 'tweets' ? state.generationPromptOptions : state.promptOptions),
    ]
    const options = allOptions()
    assert.equal(options.filter(({ disabled }) => disabled).length, 2)
    assert.ok(options.filter(({ value }) => value <= 20).every(({ disabled }) => !disabled))
    state.selectedPromptIds = state.selectedPromptIds.slice(1)
    assert.ok(allOptions().every(({ disabled }) => !disabled))
  })
}

test('tweet generation accepts prompt-only selection with the existing request contract', async (t) => {
  const { state, calls } = await setup(t, TweetsView)
  state.generateTweet = { id: 1, tweet_id: 'prompt-only' }
  state.generationSkills = [custom, github]
  state.selectedPromptIds = [11]
  assert.deepEqual(state.selectedGenerationSkillIds, [])
  await state.generate()
  const generation = calls.find(
    ([api, method]) => api === 'aiApi' && method === 'generateFromTweet',
  )
  assert.equal(generation[2], 'prompt-only')
  assert.deepEqual(generation[3].skill_ids, [11])
  assert.ok(generation[3].idempotency_key)
})

test('listening task validates prompt-only selection and rejects inactive or excessive selections', async (t) => {
  const { state } = await setup(t, AiListenTasks)
  state.skills = [custom, github]
  state.form.name = 'Prompt-only task'
  state.form.monitored_user_ids = [1]
  state.selectedPromptIds = [11]
  assert.equal(state.validate(), '')
  assert.deepEqual(state.payload().skill_ids, [11])
  state.skills = [{ ...custom, is_active: false }, github]
  assert.match(state.validate(), /停用/)
  state.form.skill_ids = Array.from({ length: 21 }, (_, index) => index + 1)
  assert.match(state.validate(), /1–20/)
})

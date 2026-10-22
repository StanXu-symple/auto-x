import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick, reactive, ref } from 'vue'
import ts from 'typescript'

const tmpRoot = new URL('../node_modules/.tmp/', import.meta.url)
mkdirSync(tmpRoot, { recursive: true })
const directory = mkdtempSync(fileURLToPath(new URL('agent-chat-', tmpRoot)))
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText
writeFileSync(
  `${directory}/utils.mjs`,
  transpile(readFileSync(new URL('../src/utils/agentChat.ts', import.meta.url), 'utf8')),
)
const utils = await import(pathToFileURL(`${directory}/utils.mjs`).href)
const { descriptor } = parse(
  readFileSync(new URL('../src/views/AgentChatView.vue', import.meta.url), 'utf8'),
)
const compiled = transpile(compileScript(descriptor, { id: 'agent-chat-test' }).content)
writeFileSync(
  `${directory}/view.mjs`,
  compiled.replace(/^import\s+.+?\s+from\s+['"]([^'"]+)['"];?$/gm, (line, module) => {
    if (module === 'vue') return line
    if (module === 'vue-router')
      return 'const useRoute = () => globalThis.chatMocks.route; const useRouter = () => globalThis.chatMocks.router;'
    if (module === '@/services/agentChat')
      return 'const agentChatApi = new Proxy({}, { get: (_, method) => (...args) => globalThis.chatMocks.call(method, args) });'
    if (module === '@/services/api')
      return 'const aiApi = { skillPage: (...args) => globalThis.chatMocks.call("skillPage", args) };'
    if (module === '@/services/http')
      return 'const getErrorMessage = (e, fallback) => e?.message || fallback;'
    if (module === '@/utils/agentChat') return line.replace('@/utils/agentChat', './utils.mjs')
    if (module === 'ant-design-vue')
      return 'const message = { info() {}, error() {}, warning() {}, success() {} };'
    if (module === '@ant-design/icons-vue')
      return line
        .match(/^import\s+\{([^}]+)\}/)[1]
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
        .map((icon) => `const ${icon} = null;`)
        .join('\n')
    if (module.endsWith('.vue')) return 'const PageHeader = null;'
    throw new Error(`Unexpected import ${module}`)
  }),
)
const { default: View } = await import(pathToFileURL(`${directory}/view.mjs`).href)
View.render = () => null
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
  delete globalThis.chatMocks
  rmSync(directory, { recursive: true, force: true })
})
const tick = () => new Promise((resolve) => setImmediate(resolve))
const prompt = { id: 1, name: '短文提示词', content_type: 'prompt', version: 2, is_active: true }
const skill = { id: 2, name: '写作 Skill', content_type: 'skill', version: 3, is_active: true }

async function setup(t, extra = {}) {
  const calls = []
  const route = reactive({ query: {} })
  const router = {
    async replace({ query }) {
      route.query = query
      await nextTick()
    },
  }
  globalThis.chatMocks = {
    route,
    router,
    async call(method, args) {
      calls.push([method, ...args])
      if (extra[method]) return extra[method](...args)
      if (method === 'list') return { items: [], total: 0 }
      if (method === 'skillPage') return { items: [prompt, skill], total: 2 }
      if (method === 'create') return { id: 'conversation-one', title: '新对话' }
      if (method === 'send')
        return { ...args[1], id: 'turn-one', status: 'queued', skill_snapshot: [], article: null }
      if (method === 'detail') return { id: args[0], title: args[0], turns: [] }
      if (method === 'save') return { article_id: '123' }
    },
  }
  const instance = ref()
  const app = renderer.createApp({ render: () => h(View, { ref: instance }) })
  app.mount({})
  t.after(() => app.unmount())
  await tick()
  return { state: instance.value.$.setupState, route, calls }
}

test('@ only opens at a token boundary and removes the selected token at the caret', () => {
  assert.equal(utils.mentionAt('mail@example.com', 16), null)
  const value = '写一篇 @短文，保留后面的文字'
  const mention = utils.mentionAt(value, 7)
  assert.deepEqual(mention, { start: 4, end: 7, query: '短文' })
  assert.equal(utils.removeMention(value, mention), '写一篇 ，保留后面的文字')
})

test('select both resource types, deduplicate, remove, and send associated IDs', async (t) => {
  const { state, calls } = await setup(t)
  state.text = '写文章 @短'
  state.updateMention()
  assert.deepEqual(
    state.candidates.map((item) => item.id),
    [1],
  )
  await state.selectSkill(prompt)
  await state.selectSkill(prompt)
  await state.selectSkill(skill)
  assert.equal(state.text, '写文章 ')
  assert.deepEqual(
    state.selected.map((item) => item.content_type),
    ['prompt', 'skill'],
  )
  state.selected = state.selected.filter((item) => item.id !== 1)
  await state.send()
  assert.deepEqual(calls.find(([method]) => method === 'send')[2].skill_ids, [2])
  assert.equal(state.text, '')
  assert.equal(state.detail.turns[0].status, 'queued')
  state.text = '新的要求'
  assert.equal(!!state.canSend, false)
})

test('a lost send response preserves text and reuses the idempotency key on retry', async (t) => {
  let attempts = 0
  const { state, calls } = await setup(t, {
    send: async (_id, body) => {
      if (++attempts === 1) throw new Error('network')
      return { ...body, id: 'one', status: 'queued' }
    },
  })
  state.text = '文章要求'
  await state.send()
  assert.equal(state.text, '文章要求')
  await state.send()
  const sends = calls.filter(([method]) => method === 'send')
  assert.equal(sends.length, 2)
  assert.equal(sends[0][2].request_id, sends[1][2].request_id)
  assert.equal(calls.filter(([method]) => method === 'create').length, 1)
})

test('old polling response cannot overwrite a different conversation', async (t) => {
  let release
  const { state, route } = await setup(t, {
    detail: async (id) =>
      id === 'old'
        ? new Promise((resolve) => {
            release = resolve
          })
        : { id, title: 'new', turns: [] },
  })
  route.query = { conversation: 'old' }
  await nextTick()
  route.query = { conversation: 'new' }
  await tick()
  release({ id: 'old', title: 'stale', turns: [] })
  await tick()
  assert.equal(state.detail.id, 'new')
  assert.equal(state.loading, false)
})

test('all pages of references are available and IME Enter does not send', async (t) => {
  const { state, calls } = await setup(t, {
    skillPage: async ({ page }) => ({ items: page === 1 ? [prompt] : [skill], total: 2 }),
  })
  assert.equal(state.skills.length, 2)
  state.text = '写文章'
  state.keydown({ isComposing: true, key: 'Enter', ctrlKey: true })
  await tick()
  assert.equal(
    calls.some(([method]) => method === 'send'),
    false,
  )
})

test('article save updates the card and references are restored from the last message', async (t) => {
  const { state, route } = await setup(t, {
    detail: async (id) => ({
      id,
      title: '文章',
      turns: [
        {
          id: 'turn-one',
          skill_snapshot: [prompt, skill],
          status: 'succeeded',
          article: { title: '标题', content: '正文', excerpt: '' },
        },
      ],
    }),
  })
  route.query = { conversation: 'one' }
  await tick()
  assert.deepEqual(
    state.selected.map((item) => item.id),
    [1, 2],
  )
  await state.turnAction(state.detail.turns[0], 'save')
  assert.equal(state.detail.turns[0].article_id, '123')
})

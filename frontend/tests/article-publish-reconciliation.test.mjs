import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, ref } from 'vue'
import ts from 'typescript'

const tmpRoot = new URL('../node_modules/.tmp/', import.meta.url)
mkdirSync(tmpRoot, { recursive: true })
const directory = mkdtempSync(fileURLToPath(new URL('article-reconcile-', tmpRoot)))
const utilityPath = `${directory}/publishReconciliation.mjs`
const compiledPath = `${directory}/ArticlesView.mjs`
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText
writeFileSync(
  utilityPath,
  transpile(
    readFileSync(new URL('../src/utils/publishReconciliation.ts', import.meta.url), 'utf8'),
  ),
)
const { safeXhsPublishedUrl } = await import(pathToFileURL(utilityPath).href)
const { descriptor } = parse(
  readFileSync(new URL('../src/views/ArticlesView.vue', import.meta.url), 'utf8'),
)
const compiled = transpile(compileScript(descriptor, { id: 'article-reconcile-test' }).content)
writeFileSync(
  compiledPath,
  compiled.replace(/^import\s+.+?\s+from\s+['"]([^'"]+)['"];?$/gm, (line, module) => {
    if (module === 'vue') return line
    if (module === '@/composables/usePagedTable')
      return 'const usePagedTable = () => globalThis.articleReconcileMocks.usePagedTable();'
    if (module === '@/composables/useXhsVerification')
      return 'const useXhsVerification = () => ({ open: false, image: null, start() {}, stop() {} });'
    if (module === '@/services/api')
      return `const aiApi = { reconcilePublishDispatch: (...args) => globalThis.articleReconcileMocks.reconcile(...args) };
        const articlesApi = {}; const qqApi = {}; const xhsApi = { verification() {} };`
    if (module === '@/services/http')
      return 'const getErrorMessage = (error, fallback) => error?.message || fallback;'
    if (module === '@/utils/format') return 'const formatDateTime = () => "";'
    if (module === '@/utils/publishReconciliation')
      return `import { reconciliationLabels, safeXhsPublishedUrl } from ${JSON.stringify(pathToFileURL(utilityPath).href)};`
    if (module === 'ant-design-vue')
      return `const message = new Proxy({}, { get: (_target, method) => (...args) => globalThis.articleReconcileMocks.messages.push([method, ...args]) });
        const Modal = { confirm() {} };`
    if (module === '@ant-design/icons-vue')
      return line
        .match(/^import\s+\{([^}]+)\}/)[1]
        .split(',')
        .map((name) => name.trim())
        .filter(Boolean)
        .map((name) => `const ${name} = null;`)
        .join('\n')
    if (module.endsWith('.vue'))
      return `const ${line.match(/^import\s+([\w$]+)\s+from/)[1]} = null;`
    throw new Error(`Unexpected ArticlesView import: ${module}`)
  }),
)
const { default: ArticlesView } = await import(pathToFileURL(compiledPath).href)
ArticlesView.render = () => null
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
  delete globalThis.articleReconcileMocks
  rmSync(directory, { recursive: true, force: true })
})

const article = (status = 'uncertain', channel = 'xhs') => ({
  id: 42,
  title: '待核对文章',
  auto_publish_dispatches: [{ id: 51, channel, status, last_error: '连接中断' }],
})

function setup(t, reconcile) {
  const calls = []
  const messages = []
  let loads = 0
  globalThis.articleReconcileMocks = {
    messages,
    reconcile: async (...args) => {
      calls.push(args)
      return reconcile?.(...args)
    },
    usePagedTable: () => ({
      rows: ref([]),
      total: ref(0),
      loading: ref(false),
      pagination: ref({}),
      load: async () => {
        loads++
      },
      reset: async () => {},
      change: async () => {},
    }),
  }
  const instance = ref()
  const app = renderer.createApp({ render: () => h(ArticlesView, { ref: instance }) })
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

test('only HTTPS Xiaohongshu links are allowed', () => {
  for (const value of ['https://www.xiaohongshu.com/explore/abc', 'https://xhslink.com/a/abc'])
    assert.ok(safeXhsPublishedUrl(value))
  for (const value of [
    'javascript:alert(1)',
    'http://xiaohongshu.com/explore/abc',
    'https://xiaohongshu.com.attacker.example/abc',
    'https://attacker.example/xhslink.com/abc',
    'https://user:secret@xiaohongshu.com/abc',
    'https://xiaohongshu.com:8443/explore/abc',
    'https://xiaohongshu.com/',
    'https://xiaohongshu.com/explore/a\nbc',
    'not-a-url',
  ])
    assert.equal(safeXhsPublishedUrl(value), undefined)
  const prefix = 'https://www.xiaohongshu.com/explore/'
  const longest = prefix + 'a'.repeat(2048 - prefix.length)
  assert.ok(safeXhsPublishedUrl(longest))
  assert.equal(safeXhsPublishedUrl(`${longest}a`), undefined)
})

test('reconciliation opens only for uncertain XHS results and clears previous inputs', (t) => {
  const { state } = setup(t)
  state.prepareReconcile(article('published'))
  state.prepareReconcile(article('uncertain', 'qq'))
  assert.equal(state.reconcileOpen, false)
  state.prepareReconcile(article())
  assert.equal(state.reconcileOpen, true)
  assert.equal(state.reconcileDispatch.id, 51)
  state.reconcileForm.note = '上次说明'
  state.reconcileForm.decision = 'published'
  state.prepareReconcile(article())
  assert.equal(state.reconcileForm.note, '')
  assert.equal(state.reconcileForm.decision, 'uncertain')
})

test('manual republish shows its current result while preserving other automatic channels', (t) => {
  const { state } = setup(t)
  const original = {
    ...article('failed'),
    publish_channel: 'xhs',
    publish_status: 'published',
    auto_publish_dispatches: [
      { id: 51, channel: 'xhs', status: 'failed', reconciliation_result: 'not_published' },
      { id: 52, channel: 'qq', status: 'published' },
    ],
  }
  assert.equal(state.hasManualPublish(original), true)
  assert.deepEqual(
    state.visibleAutoDispatches(original).map((dispatch) => dispatch.id),
    [52],
  )
  assert.equal(state.manualPublishLabel(original), '小红书（手动）：已发布')
  original.publish_status = 'queued'
  assert.equal(state.manualPublishLabel(original), '小红书（手动）：发布中')
  original.publish_status = 'unpublished'
  assert.equal(state.hasManualPublish(original), false)
  assert.equal(state.visibleAutoDispatches(original).length, 2)
  assert.equal(original.auto_publish_dispatches.length, 2)
})

test('published decision requires a valid note URL before saving', async (t) => {
  const { state, calls, messages } = setup(t)
  state.prepareReconcile(article())
  state.reconcileForm.decision = 'published'
  await state.submitReconcile()
  state.reconcileForm.published_url = 'https://attacker.example/'
  await state.submitReconcile()
  assert.equal(calls.length, 0)
  assert.equal(messages.filter(([method]) => method === 'warning').length, 2)
  assert.equal(state.reconcileOpen, true)
})

test('published decision saves the selected dispatch, trims metadata and refreshes the list', async (t) => {
  const result = setup(t)
  result.state.prepareReconcile(article())
  Object.assign(result.state.reconcileForm, {
    decision: 'published',
    published_url: ' https://www.xiaohongshu.com/explore/abc ',
    note: ' 已查笔记列表 ',
  })
  await result.state.submitReconcile()
  assert.deepEqual(result.calls, [
    [
      51,
      {
        decision: 'published',
        published_url: 'https://www.xiaohongshu.com/explore/abc',
        note: '已查笔记列表',
      },
    ],
  ])
  assert.equal(result.state.reconcileOpen, false)
  assert.equal(result.loads, 2)
})

test('not published and uncertain can be recorded without a note or stale URL', async (t) => {
  const { state, calls } = setup(t)
  for (const decision of ['not_published', 'uncertain']) {
    state.prepareReconcile(article())
    Object.assign(state.reconcileForm, { decision, published_url: 'https://xhslink.com/old' })
    await state.submitReconcile()
  }
  assert.deepEqual(calls, [
    [51, { decision: 'not_published', published_url: null, note: null }],
    [51, { decision: 'uncertain', published_url: null, note: null }],
  ])
})

test('saving prevents duplicate requests and preserves input after a conflict', async (t) => {
  let reject
  const pending = new Promise((_, no) => {
    reject = no
  })
  const { state, calls, messages } = setup(t, () => pending)
  state.prepareReconcile(article())
  state.reconcileForm.note = '待再次确认'
  const first = state.submitReconcile()
  await state.submitReconcile()
  assert.equal(calls.length, 1)
  assert.equal(state.reconcileSubmitting, true)
  reject(new Error('记录已更新，请刷新后重试'))
  await first
  assert.equal(state.reconcileSubmitting, false)
  assert.equal(state.reconcileOpen, true)
  assert.equal(state.reconcileForm.note, '待再次确认')
  assert.deepEqual(messages.at(-1), ['error', '记录已更新，请刷新后重试'])
})

import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'
import ts from 'typescript'

const tmpRoot = new URL('../node_modules/.tmp/', import.meta.url)
mkdirSync(tmpRoot, { recursive: true })
const directory = mkdtempSync(fileURLToPath(new URL('article-edit-media-', tmpRoot)))
const compiledPath = `${directory}/ArticlesView.mjs`
const source = readFileSync(new URL('../src/views/ArticlesView.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = ts.transpileModule(
  compileScript(descriptor, { id: 'article-edit-media-test' }).content,
  {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  },
).outputText

function mockImport(line, module) {
  if (module === 'vue') return line
  if (module === '@/composables/usePagedTable')
    return 'const usePagedTable = (...args) => globalThis.articleEditMediaMocks.usePagedTable(...args);'
  if (module === '@/composables/useXhsVerification')
    return 'const useXhsVerification = (...args) => globalThis.articleEditMediaMocks.useXhsVerification(...args);'
  if (module === '@/services/api')
    return `const articlesApi = new Proxy({}, { get: (_target, method) => (...args) => globalThis.articleEditMediaMocks.api[method](...args) });
      const qqApi = {}; const xhsApi = { verification() {} };`
  if (module === '@/services/http')
    return 'const getErrorMessage = (error, fallback) => error?.message || fallback;'
  if (module === '@/utils/format') return 'const formatDateTime = () => "";'
  if (module === 'ant-design-vue')
    return `const message = new Proxy({}, { get: (_target, method) => (...args) => globalThis.articleEditMediaMocks.messages.push([method, ...args]) });
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
  throw new Error(`Unexpected ArticlesView import: ${module}`)
}

writeFileSync(
  compiledPath,
  compiled.replace(/^import\s+.+?\s+from\s+['"]([^'"]+)['"];?$/gm, (line, module) =>
    mockImport(line, module),
  ),
)
const { default: ArticlesView } = await import(pathToFileURL(compiledPath).href)
// Exercise the real view setup without rendering Ant Design or making network requests.
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
  delete globalThis.articleEditMediaMocks
  rmSync(directory, { recursive: true, force: true })
})

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

function article(overrides = {}) {
  return {
    id: 42,
    revision: 7,
    title: '原文章',
    content: '正文',
    excerpt: '',
    images: ['7/first.png', '7/second.png'],
    source_tweet_id: 11,
    source_screenshot: { tweet_id: '123', sha256: 'abc' },
    include_source_screenshot: true,
    ...overrides,
  }
}

function setup(t, api = {}) {
  const calls = []
  const messages = []
  let loads = 0
  globalThis.articleEditMediaMocks = {
    api: {
      update: async (...args) => calls.push(args),
      upload: async () => ({ files: [] }),
      ...api,
    },
    messages,
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
    useXhsVerification: () => ({
      open: ref(false),
      image: ref(null),
      start() {},
      stop() {},
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

test('edit stages both photo deletions without mutating the article row', (t) => {
  const { state } = setup(t)
  const original = article()
  state.edit(original)
  assert.notStrictEqual(state.form.images, original.images)
  assert.equal(state.sourceScreenshot.tweet_id, '123')
  assert.equal(state.mediaCount, 3)

  state.removeImage('7/first.png')
  state.removeImage('7/second.png')
  state.removeSourceScreenshot()
  assert.deepEqual(state.form.images, [])
  assert.equal(state.includeSourceScreenshot, false)
  assert.equal(state.sourceScreenshot, null)
  assert.equal(state.mediaCount, 0)
  assert.deepEqual(original.images, ['7/first.png', '7/second.png'])
  assert.equal(original.include_source_screenshot, true)
  assert.equal(original.source_tweet_id, 11)
})

test('saving staged removals sends an explicit empty image list and false screenshot flag', async (t) => {
  const { state, calls } = setup(t)
  state.edit(article())
  state.removeImage('7/first.png')
  state.removeImage('7/second.png')
  state.removeSourceScreenshot()
  await state.save()

  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], 42)
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])), {
    title: '原文章',
    content: '正文',
    excerpt: '',
    images: [],
    include_source_screenshot: false,
    revision: 7,
  })
  assert.notStrictEqual(calls[0][1].images, state.form.images)
  assert.equal(state.open, false)
})

test('an uploaded photo and linked screenshot are only removed from the edit draft', async (t) => {
  const { state, calls } = setup(t, {
    upload: async () => ({ files: [{ path: '7/new.png' }] }),
  })
  const original = article()
  state.edit(original)
  await state.upload({ target: { files: [{ name: 'new.png' }], value: 'new.png' } })
  assert.deepEqual(state.form.images, ['7/first.png', '7/second.png', '7/new.png'])
  state.removeImage('7/new.png')
  state.removeSourceScreenshot()
  assert.deepEqual(state.form.images, original.images)
  assert.equal(state.sourceScreenshot, null)
  assert.equal(state.open, true)
  assert.deepEqual(calls, [])
  assert.equal(original.include_source_screenshot, true)
})

test('cancel and reopen restore photos; a previously excluded screenshot can be restored', (t) => {
  const { state } = setup(t)
  const original = article()
  state.edit(original)
  state.removeImage('7/first.png')
  state.removeSourceScreenshot()
  state.open = false // Modal cancel discards the local draft.
  state.edit(original)
  assert.deepEqual(state.form.images, original.images)
  assert.equal(state.includeSourceScreenshot, true)
  assert.equal(state.sourceScreenshot.tweet_id, '123')

  const excluded = article({ include_source_screenshot: false, source_screenshot: null })
  state.edit(excluded)
  assert.equal(state.sourceScreenshot, null)
  assert.equal(state.mediaCount, 2)
  state.restoreSourceScreenshot()
  assert.equal(state.includeSourceScreenshot, true)
  assert.equal(state.sourceScreenshot, null)
  assert.equal(state.mediaCount, 3)
  assert.equal(excluded.source_tweet_id, 11)
  assert.equal(excluded.include_source_screenshot, false)
})

test('restoring a source screenshot reserves the eighteenth slot before it is available', async (t) => {
  let uploadCalls = 0
  const { state, messages } = setup(t, {
    upload: async () => {
      uploadCalls++
      return { files: [{ path: '7/extra.png' }] }
    },
  })
  const images = Array.from({ length: 17 }, (_, index) => `7/image-${index}.png`)
  state.edit(article({ images, include_source_screenshot: false, source_screenshot: null }))
  assert.equal(state.mediaCount, 17)
  state.restoreSourceScreenshot()
  assert.equal(state.includeSourceScreenshot, true)
  assert.equal(state.sourceScreenshot, null)
  assert.equal(state.mediaCount, 18)
  assert.equal(state.visibleMediaCount, 17)

  await state.upload({ target: { files: [{ name: 'extra.png' }], value: 'extra.png' } })
  assert.equal(uploadCalls, 0)
  assert.deepEqual(state.form.images, images)
  assert.deepEqual(messages.at(-1), ['warning', '每篇文章最多 18 张图片（含自动关联的原帖截图）'])
})

test('restoring a source screenshot is blocked when eighteen uploaded images already fill the limit', (t) => {
  const { state, messages } = setup(t)
  const images = Array.from({ length: 18 }, (_, index) => `7/image-${index}.png`)
  state.edit(article({ images, include_source_screenshot: false, source_screenshot: null }))
  assert.equal(state.mediaCount, 18)
  state.restoreSourceScreenshot()
  assert.equal(state.includeSourceScreenshot, false)
  assert.equal(state.sourceScreenshot, null)
  assert.equal(state.mediaCount, 18)
  assert.deepEqual(messages.at(-1), ['warning', '请先删除一张图片，为原帖截图留出位置'])
})

test('a failed save leaves the dialog open with staged removals available for retry', async (t) => {
  const { state, messages } = setup(t, {
    update: async () => {
      throw new Error('revision conflict')
    },
  })
  state.edit(article())
  state.removeImage('7/first.png')
  state.removeSourceScreenshot()
  await state.save()
  assert.equal(state.open, true)
  assert.deepEqual(state.form.images, ['7/second.png'])
  assert.equal(state.includeSourceScreenshot, false)
  assert.equal(state.saving, false)
  assert.deepEqual(messages.at(-1), ['error', 'revision conflict'])
})

test('upload and save in flight block removals and a second save', async (t) => {
  const pendingUpload = deferred()
  const pendingSave = deferred()
  let saveCalls = 0
  const { state } = setup(t, {
    upload: () => pendingUpload.promise,
    update: () => {
      saveCalls++
      return pendingSave.promise
    },
  })
  state.edit(article())
  const input = { files: [{ name: 'third.png' }], value: 'third.png' }
  const uploading = state.upload({ target: input })
  assert.equal(state.uploading, true)
  state.removeImage('7/first.png')
  state.removeSourceScreenshot()
  await state.save()
  assert.deepEqual(state.form.images, ['7/first.png', '7/second.png'])
  assert.equal(state.includeSourceScreenshot, true)
  assert.equal(saveCalls, 0)
  pendingUpload.resolve({ files: [{ path: '7/third.png' }] })
  await uploading
  assert.deepEqual(state.form.images, ['7/first.png', '7/second.png', '7/third.png'])

  const saving = state.save()
  assert.equal(state.saving, true)
  state.removeImage('7/first.png')
  state.removeSourceScreenshot()
  await state.save()
  assert.equal(saveCalls, 1)
  assert.deepEqual(state.form.images, ['7/first.png', '7/second.png', '7/third.png'])
  assert.equal(state.includeSourceScreenshot, true)
  pendingSave.resolve()
  await saving
  await nextTick()
  assert.equal(state.saving, false)
})

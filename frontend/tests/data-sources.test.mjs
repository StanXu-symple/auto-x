import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'
import ts from 'typescript'

const tmpRoot = new URL('../node_modules/.tmp/', import.meta.url)
mkdirSync(tmpRoot, { recursive: true })
const directory = mkdtempSync(fileURLToPath(new URL('data-sources-', tmpRoot)))
const routerPath = `${directory}/router.mjs`
const viewPath = `${directory}/DataSourcesView.mjs`
const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText

writeFileSync(
  routerPath,
  compile(readFileSync(new URL('../src/router/index.ts', import.meta.url), 'utf8'))
    .replace('createWebHistory', 'createMemoryHistory as createWebHistory')
    .replace(
      /^import .+ from ['"]@\/stores\/auth['"];?$/m,
      'const useAuthStore = () => globalThis.dataSourcesAuth;',
    )
    .replaceAll('import.meta.env.BASE_URL', '"/"')
    .replaceAll('import.meta.env.DEV', 'false')
    .replace(/\(\) => import\('[^']+'\)/g, '() => Promise.resolve({ render: () => null })'),
)
const viewSource = readFileSync(
  new URL('../src/views/DataSourcesView.vue', import.meta.url),
  'utf8',
)
writeFileSync(
  viewPath,
  compile(compileScript(parse(viewSource).descriptor, { id: 'data-sources-test' }).content).replace(
    /^import ([\w$]+) from ['"][^'"]+\.vue['"];?$/gm,
    'const $1 = null;',
  ),
)
const { default: DataSourcesView } = await import(pathToFileURL(viewPath).href)
DataSourcesView.render = () => null
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
let routerId = 0
const originalDocument = globalThis.document
globalThis.document = { title: '' }
globalThis.dataSourcesAuth = { isAuthenticated: true }
after(() => {
  if (originalDocument === undefined) delete globalThis.document
  else globalThis.document = originalDocument
  delete globalThis.dataSourcesAuth
  rmSync(directory, { recursive: true, force: true })
})

async function routerAt(path) {
  const { default: router } = await import(`${pathToFileURL(routerPath).href}?test=${++routerId}`)
  await router.push(path)
  await router.isReady()
  return router
}

test('old data source links redirect to the matching tab while preserving query and hash', async () => {
  for (const [path, tab] of [
    ['/x-authorization', 'x'],
    ['/xhs', 'xhs'],
  ]) {
    const router = await routerAt(`${path}?tab=obsolete&filter=one&filter=two#connection`)
    assert.equal(router.currentRoute.value.path, '/data-sources')
    assert.deepEqual(router.currentRoute.value.query, { tab, filter: ['one', 'two'] })
    assert.equal(router.currentRoute.value.hash, '#connection')
    assert.equal(router.currentRoute.value.meta.title, '数据源')
  }
})

test('legacy named routes remain valid for integrations and bookmarks', async () => {
  const router = await routerAt('/dashboard')
  await router.push({ name: 'xhs', query: { from: 'article' }, hash: '#publish' })
  assert.equal(router.currentRoute.value.fullPath, '/data-sources?from=article&tab=xhs#publish')
  await router.push({ name: 'x-authorization', query: { from: 'settings' } })
  assert.equal(router.currentRoute.value.fullPath, '/data-sources?from=settings&tab=x')
})

test('tab selection follows URL navigation and preserves unrelated URL state when switching', async (t) => {
  const router = await routerAt('/data-sources?tab=xhs&from=settings#connection')
  const instance = ref()
  const app = renderer.createApp({ render: () => h(DataSourcesView, { ref: instance }) })
  app.use(router)
  app.mount({})
  t.after(() => app.unmount())
  const state = instance.value.$.setupState
  assert.equal(state.tab, 'xhs')
  state.changeTab('x')
  await new Promise((resolve) => setImmediate(resolve))
  await nextTick()
  assert.equal(router.currentRoute.value.fullPath, '/data-sources?tab=x&from=settings#connection')
  assert.equal(state.tab, 'x')
  await router.push('/data-sources?tab=xhs')
  assert.equal(state.tab, 'xhs')
  await router.push('/data-sources?tab=unknown')
  assert.equal(state.tab, 'x')
  await router.push('/data-sources')
  assert.equal(state.tab, 'x')
})

test('authentication still protects merged and legacy data source routes', async () => {
  globalThis.dataSourcesAuth.isAuthenticated = false
  try {
    const router = await routerAt('/xhs?from=article#publish')
    assert.equal(router.currentRoute.value.path, '/login')
    assert.equal(
      router.currentRoute.value.query.redirect,
      '/data-sources?from=article&tab=xhs#publish',
    )
  } finally {
    globalThis.dataSourcesAuth.isAuthenticated = true
  }
})

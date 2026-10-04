import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick, reactive, ref } from 'vue'
import ts from 'typescript'

const tmpRoot = new URL('../node_modules/.tmp/', import.meta.url)
mkdirSync(tmpRoot, { recursive: true })
const directory = mkdtempSync(fileURLToPath(new URL('article-media-', tmpRoot)))
const compiledPath = `${directory}/ArticleMediaGallery.mjs`
const source = readFileSync(
  new URL('../src/components/ArticleMediaGallery.vue', import.meta.url),
  'utf8',
)
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'article-media-test' }).content.replace(
  "import { articlesApi, tweetsApi } from '@/services/api'",
  'const articlesApi = { image: (...args) => globalThis.articleMediaTestApi.image(...args) }; const tweetsApi = { screenshot: (...args) => globalThis.articleMediaTestApi.screenshot(...args) }',
)
writeFileSync(
  compiledPath,
  ts.transpileModule(script, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText,
)
const { default: Gallery } = await import(pathToFileURL(compiledPath).href)
// Mount the real setup logic with Vue's renderer without a browser dependency.
Gallery.render = () => null
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
  delete globalThis.articleMediaTestApi
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

async function flush() {
  await nextTick()
  await Promise.resolve()
  await Promise.resolve()
}

function setup(t, props, api) {
  globalThis.articleMediaTestApi = api
  const created = []
  const revoked = []
  t.mock.method(URL, 'createObjectURL', (blob) => {
    created.push(blob)
    return `blob:test-${created.length}`
  })
  t.mock.method(URL, 'revokeObjectURL', (url) => revoked.push(url))
  const inputs = reactive(props)
  const gallery = ref()
  const removedImages = []
  let removedScreenshots = 0
  const app = renderer.createApp({
    render: () =>
      h(Gallery, {
        ...inputs,
        ref: gallery,
        onRemoveImage: (path) => removedImages.push(path),
        onRemoveSourceScreenshot: () => removedScreenshots++,
      }),
  })
  app.mount({})
  const state = gallery.value.$.setupState
  let mounted = true
  function unmount() {
    if (mounted) app.unmount()
    mounted = false
  }
  t.after(unmount)
  return {
    inputs,
    unmount,
    state,
    created,
    revoked,
    removedImages,
    get removedScreenshots() {
      return removedScreenshots
    },
  }
}

test('an article with only a source screenshot loads its authenticated screenshot media', async (t) => {
  const screenshotIds = []
  const imagePaths = []
  const state = setup(
    t,
    { images: [], sourceScreenshot: { tweet_id: '123' } },
    {
      screenshot: async (id) => {
        screenshotIds.push(id)
        return new Blob(['screenshot'])
      },
      image: async (path) => {
        imagePaths.push(path)
        return new Blob(['upload'])
      },
    },
  )
  await flush()
  assert.deepEqual(screenshotIds, ['123'])
  assert.deepEqual(imagePaths, [])
  assert.equal(state.state.media.length, 1)
  assert.equal(state.state.media[0].label, '原帖截图 · 自动关联')
  assert.equal(state.state.urls['screenshot:123'], 'blob:test-1')
  assert.deepEqual(state.inputs.images, [])
})

test('source screenshot and uploaded images use their own media APIs and revoke URLs on close', async (t) => {
  const calls = []
  const state = setup(
    t,
    { images: ['upload.png'], sourceScreenshot: { tweet_id: '123' } },
    {
      screenshot: async (id) => {
        calls.push(['screenshot', id])
        return new Blob(['screenshot'])
      },
      image: async (path) => {
        calls.push(['image', path])
        return new Blob(['upload'])
      },
    },
  )
  await flush()
  assert.deepEqual(calls, [
    ['screenshot', '123'],
    ['image', 'upload.png'],
  ])
  assert.equal(Object.keys(state.state.urls).length, 2)
  state.unmount()
  assert.deepEqual(state.revoked, ['blob:test-1', 'blob:test-2'])
  assert.deepEqual(state.state.urls, {})
})

test('switching articles discards a previous screenshot response', async (t) => {
  const oldScreenshot = deferred()
  const newScreenshot = deferred()
  const state = setup(
    t,
    { images: [], sourceScreenshot: { tweet_id: 'old' } },
    {
      screenshot: (id) => (id === 'old' ? oldScreenshot.promise : newScreenshot.promise),
      image: async () => new Blob(),
    },
  )
  state.inputs.sourceScreenshot = { tweet_id: 'new' }
  await flush()
  newScreenshot.resolve(new Blob(['new']))
  await flush()
  oldScreenshot.resolve(new Blob(['old']))
  await flush()
  assert.equal(state.created.length, 1)
  assert.deepEqual(state.state.urls, { 'screenshot:new': 'blob:test-1' })
  assert.deepEqual(state.state.failed, {})
})

test('a recaptured source screenshot refreshes its image and releases the older URL', async (t) => {
  let calls = 0
  const state = setup(
    t,
    { images: [], sourceScreenshot: { tweet_id: '123', sha256: 'first' } },
    {
      screenshot: async () => {
        calls++
        return new Blob()
      },
      image: async () => new Blob(),
    },
  )
  await flush()
  state.inputs.sourceScreenshot = { tweet_id: '123', sha256: 'second' }
  await flush()
  assert.equal(calls, 2)
  assert.deepEqual(state.revoked, ['blob:test-1'])
  assert.equal(state.state.urls['screenshot:123'], 'blob:test-2')
})

test('closing the gallery discards in-flight media requests', async (t) => {
  const pending = deferred()
  const state = setup(
    t,
    { images: [], sourceScreenshot: { tweet_id: '123' } },
    {
      screenshot: () => pending.promise,
      image: async () => new Blob(),
    },
  )
  state.unmount()
  pending.resolve(new Blob())
  await flush()
  assert.equal(state.created.length, 0)
  assert.deepEqual(state.state.urls, {})
})

test('editable gallery identifies uploaded and source photos without mutating its input', async (t) => {
  const gallery = setup(
    t,
    { images: ['1/upload.png'], sourceScreenshot: { tweet_id: '123' }, removable: true },
    { image: async () => new Blob(), screenshot: async () => new Blob() },
  )
  await flush()
  gallery.state.removeMedia(gallery.state.media[1])
  gallery.state.removeMedia(gallery.state.media[0])
  assert.deepEqual(gallery.removedImages, ['1/upload.png'])
  assert.equal(gallery.removedScreenshots, 1)
  assert.deepEqual(gallery.inputs.images, ['1/upload.png'])
  assert.equal(gallery.inputs.sourceScreenshot.tweet_id, '123')
})

test('read-only and busy galleries reject deletion events', async (t) => {
  const gallery = setup(
    t,
    { images: ['1/upload.png'], sourceScreenshot: { tweet_id: '123' } },
    { image: async () => new Blob(), screenshot: async () => new Blob() },
  )
  await flush()
  for (const item of gallery.state.media) gallery.state.removeMedia(item)
  gallery.inputs.removable = true
  gallery.inputs.disabled = true
  await flush()
  for (const item of gallery.state.media) gallery.state.removeMedia(item)
  assert.deepEqual(gallery.removedImages, [])
  assert.equal(gallery.removedScreenshots, 0)
})

test('removing a photo discards its pending preview and revokes loaded previews', async (t) => {
  const pending = deferred()
  const gallery = setup(
    t,
    { images: ['1/upload.png'], sourceScreenshot: { tweet_id: '123' }, removable: true },
    { image: () => pending.promise, screenshot: async () => new Blob() },
  )
  await flush()
  gallery.inputs.images = []
  gallery.inputs.sourceScreenshot = null
  await flush()
  pending.resolve(new Blob())
  await flush()
  assert.deepEqual(gallery.revoked, ['blob:test-1'])
  assert.deepEqual(gallery.state.urls, {})
  assert.equal(gallery.created.length, 1)
})

// Plain Node tests (no runner dependency):
//   cd frontend && node --test src/components/chat/frontmatter.test.js
import test from 'node:test'
import assert from 'node:assert/strict'
import { splitFrontmatter } from './frontmatter.js'

const types = (segs) => segs.map((s) => s.type)

test('leading frontmatter becomes a card, the body stays markdown', () => {
  const segs = splitFrontmatter('---\ntype: task-ledger\nstatus: active\ntags: [task-ledger]\n---\n\n# Ledger\n\n- [done] step 1\n')
  assert.deepEqual(types(segs), ['frontmatter', 'markdown'])
  assert.deepEqual(segs[0].fields, [['type', 'task-ledger'], ['status', 'active'], ['tags', '[task-ledger]']])
  assert.equal(segs[0].partial, false)
  assert.equal(segs[1].text.trim(), '# Ledger\n\n- [done] step 1')
})

test('embedded and indented block (as in the task prompt) is detected', () => {
  const text = 'Begin with EXACTLY this YAML frontmatter:\n  ---\n  type: task-ledger\n  task: Marble Maze\n  ---\n- Body: one line per subtask\n'
  const segs = splitFrontmatter(text)
  assert.deepEqual(types(segs), ['markdown', 'frontmatter', 'markdown'])
  assert.deepEqual(segs[1].fields, [['type', 'task-ledger'], ['task', 'Marble Maze']])
  assert.equal(segs[0].text, 'Begin with EXACTLY this YAML frontmatter:')
  assert.equal(segs[2].text.trim(), '- Body: one line per subtask')
})

test('a lone --- horizontal rule is left to markdown', () => {
  for (const text of ['intro\n\n---\n\n## Next section\n', '---\n\nSummary: a bugfix release\n1. MPS\n', 'a\n---\nb\n']) {
    const segs = splitFrontmatter(text)
    assert.deepEqual(types(segs), ['markdown'], text)
    assert.equal(segs[0].text, text)
  }
})

test('indented continuation lines belong to the previous key', () => {
  const segs = splitFrontmatter('---\nsteps:\n  - one\n  - two\nowner: me\n---\n')
  assert.deepEqual(segs[0].fields, [['steps', '- one\n- two'], ['owner', 'me']])
})

test('an unclosed block only counts while streaming', () => {
  const open = 'Saving the ledger:\n\n---\ntype: task-ledger\nstatus: acti'
  assert.deepEqual(types(splitFrontmatter(open)), ['markdown'])
  const segs = splitFrontmatter(open, { streaming: true })
  assert.deepEqual(types(segs), ['markdown', 'frontmatter'])
  assert.equal(segs[1].partial, true)
  assert.deepEqual(segs[1].fields, [['type', 'task-ledger'], ['status', 'acti']])
  // half-typed closing dashes never become a setext underline
  const dashes = splitFrontmatter(open + 've\n--', { streaming: true })
  assert.equal(dashes[1].partial, true)
  assert.deepEqual(dashes[1].fields, [['type', 'task-ledger'], ['status', 'active']])
})

test('plain text and text without dashes pass through untouched', () => {
  assert.deepEqual(splitFrontmatter('hello **world**'), [{ type: 'markdown', text: 'hello **world**' }])
  assert.deepEqual(splitFrontmatter(null), [{ type: 'markdown', text: '' }])
})

// Syntax highlighting for the code viewer/editor (UI review #28): pick a
// CodeMirror language from the file extension (falling back to the content
// type) and load it lazily, so the parsers only download when a file of that
// kind is opened. Lezer languages where installed; @codemirror/legacy-modes
// (one small stream parser per language) for the rest, Swift included.

const legacy = (load) => async () => {
  const [{ StreamLanguage }, parser] = await Promise.all([import('@codemirror/language'), load()])
  return [StreamLanguage.define(parser)]
}

const LOADERS = {
  markdown: async () => [(await import('@codemirror/lang-markdown')).markdown()],
  json: async () => [(await import('@codemirror/lang-json')).json()],
  python: async () => [(await import('@codemirror/lang-python')).python()],
  javascript: async () => [(await import('@codemirror/lang-javascript')).javascript({ jsx: true })],
  typescript: async () => [(await import('@codemirror/lang-javascript')).javascript({ jsx: true, typescript: true })],
  html: async () => [(await import('@codemirror/lang-html')).html()],
  css: async () => [(await import('@codemirror/lang-css')).css()],
  swift: legacy(async () => (await import('@codemirror/legacy-modes/mode/swift')).swift),
  c: legacy(async () => (await import('@codemirror/legacy-modes/mode/clike')).c),
  cpp: legacy(async () => (await import('@codemirror/legacy-modes/mode/clike')).cpp),
  objectivec: legacy(async () => (await import('@codemirror/legacy-modes/mode/clike')).objectiveC),
  java: legacy(async () => (await import('@codemirror/legacy-modes/mode/clike')).java),
  kotlin: legacy(async () => (await import('@codemirror/legacy-modes/mode/clike')).kotlin),
  csharp: legacy(async () => (await import('@codemirror/legacy-modes/mode/clike')).csharp),
  scala: legacy(async () => (await import('@codemirror/legacy-modes/mode/clike')).scala),
  dart: legacy(async () => (await import('@codemirror/legacy-modes/mode/clike')).dart),
  rust: legacy(async () => (await import('@codemirror/legacy-modes/mode/rust')).rust),
  go: legacy(async () => (await import('@codemirror/legacy-modes/mode/go')).go),
  shell: legacy(async () => (await import('@codemirror/legacy-modes/mode/shell')).shell),
  yaml: legacy(async () => (await import('@codemirror/legacy-modes/mode/yaml')).yaml),
  toml: legacy(async () => (await import('@codemirror/legacy-modes/mode/toml')).toml),
  ruby: legacy(async () => (await import('@codemirror/legacy-modes/mode/ruby')).ruby),
  sql: legacy(async () => (await import('@codemirror/legacy-modes/mode/sql')).standardSQL),
  dockerfile: legacy(async () => (await import('@codemirror/legacy-modes/mode/dockerfile')).dockerFile),
  diff: legacy(async () => (await import('@codemirror/legacy-modes/mode/diff')).diff),
  properties: legacy(async () => (await import('@codemirror/legacy-modes/mode/properties')).properties),
  xml: legacy(async () => (await import('@codemirror/legacy-modes/mode/xml')).xml),
}

const EXT_TO_LANG = {
  md: 'markdown', markdown: 'markdown', mdx: 'markdown',
  json: 'json', jsonc: 'json', webmanifest: 'json',
  py: 'python', pyi: 'python',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript', mts: 'typescript', cts: 'typescript',
  html: 'html', htm: 'html', vue: 'html', svelte: 'html',
  css: 'css', scss: 'css', less: 'css',
  swift: 'swift',
  c: 'c', h: 'c',
  cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp', hxx: 'cpp',
  m: 'objectivec', mm: 'objectivec',
  java: 'java', kt: 'kotlin', kts: 'kotlin', cs: 'csharp', scala: 'scala', dart: 'dart',
  rs: 'rust', go: 'go',
  sh: 'shell', bash: 'shell', zsh: 'shell', env: 'shell',
  yml: 'yaml', yaml: 'yaml', toml: 'toml',
  rb: 'ruby', sql: 'sql',
  diff: 'diff', patch: 'diff',
  ini: 'properties', cfg: 'properties', conf: 'properties', properties: 'properties',
  xml: 'xml', svg: 'xml', plist: 'xml', xib: 'xml', storyboard: 'xml',
}

const NAME_TO_LANG = { dockerfile: 'dockerfile', makefile: 'shell', 'podfile': 'ruby', 'gemfile': 'ruby' }

const MIME_TO_LANG = {
  'text/markdown': 'markdown',
  'application/json': 'json',
  'text/x-python': 'python',
  'text/javascript': 'javascript',
  'application/javascript': 'javascript',
  'text/typescript': 'typescript',
  'text/html': 'html',
  'text/css': 'css',
  'application/yaml': 'yaml',
  'text/yaml': 'yaml',
  'application/xml': 'xml',
  'text/xml': 'xml',
  'image/svg+xml': 'xml',
  'text/x-swift': 'swift',
  'text/x-c': 'c',
  'text/x-c++': 'cpp',
  'text/x-java': 'java',
  'text/x-rust': 'rust',
  'text/x-go': 'go',
  'text/x-sh': 'shell',
  'text/x-shellscript': 'shell',
  'application/toml': 'toml',
  'text/x-ruby': 'ruby',
  'application/sql': 'sql',
  'text/x-diff': 'diff',
}

/** Language id for a file name and/or content type ('text' when unknown). */
export function detectLanguage(filename, contentType) {
  const base = String(filename || '').split('/').pop().toLowerCase()
  if (NAME_TO_LANG[base]) return NAME_TO_LANG[base]
  const dot = base.lastIndexOf('.')
  if (dot >= 0) {
    const lang = EXT_TO_LANG[base.slice(dot + 1)]
    if (lang) return lang
  }
  const ct = String(contentType || '').split(';')[0].trim().toLowerCase()
  return MIME_TO_LANG[ct] || 'text'
}

const cache = new Map()

/** Promise of the CodeMirror extensions for a language id ([] for plain text). */
export function loadLanguage(lang) {
  const loader = LOADERS[lang]
  if (!loader) return Promise.resolve([])
  if (!cache.has(lang)) {
    cache.set(lang, loader().catch((e) => {
      cache.delete(lang)
      console.warn('failed to load language', lang, e)
      return []
    }))
  }
  return cache.get(lang)
}

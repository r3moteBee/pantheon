// Syntax highlighting for fenced code in the shared Markdown renderer.
// Loaded lazily by CodeBlock (dynamic import), so the Lezer parsers only
// download once a highlighted block is on screen. Uses the Lezer grammars
// the CodeMirror editor already depends on - no extra highlighter library.
import { highlightCode, tagHighlighter, tags as t } from '@lezer/highlight'
import { parser as jsParser } from '@lezer/javascript'
import { parser as pyParser } from '@lezer/python'
import { parser as jsonParser } from '@lezer/json'
import { parser as cssParser } from '@lezer/css'
import { parser as htmlParser } from '@lezer/html'

const PARSERS = {
  javascript: jsParser.configure({ dialect: 'jsx' }),
  typescript: jsParser.configure({ dialect: 'ts' }),
  tsx: jsParser.configure({ dialect: 'jsx ts' }),
  python: pyParser,
  json: jsonParser,
  css: cssParser,
  html: htmlParser,
}

const ALIASES = {
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript', javascript: 'javascript',
  ts: 'typescript', typescript: 'typescript', tsx: 'tsx',
  py: 'python', python: 'python', python3: 'python',
  json: 'json', jsonc: 'json',
  css: 'css', scss: 'css',
  html: 'html', htm: 'html', xml: 'html', svg: 'html', vue: 'html',
}

export function supportedLanguage(lang) {
  return ALIASES[(lang || '').toLowerCase()] || null
}

// Colours chosen for >= 4.5:1 on the gray-900 code background.
const highlighter = tagHighlighter([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword, t.definitionKeyword, t.modifier], class: 'text-violet-300' },
  { tag: [t.string, t.special(t.string), t.regexp, t.attributeValue], class: 'text-emerald-300' },
  { tag: [t.number, t.bool, t.null, t.atom], class: 'text-amber-300' },
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], class: 'text-gray-400 italic' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.macroName], class: 'text-sky-300' },
  { tag: [t.definition(t.variableName), t.definition(t.propertyName)], class: 'text-sky-200' },
  { tag: [t.typeName, t.className, t.namespace, t.tagName], class: 'text-rose-300' },
  { tag: [t.propertyName, t.attributeName], class: 'text-cyan-200' },
  { tag: [t.self, t.standard(t.variableName)], class: 'text-orange-300' },
])

/** Highlighted segments [{text, cls}] (cls '' = plain), or null if the language isn't supported. */
export function highlight(code, lang) {
  const name = supportedLanguage(lang)
  if (!name || code.length > 100_000) return null
  const tree = PARSERS[name].parse(code)
  const out = []
  highlightCode(code, tree, highlighter,
    (text, cls) => out.push({ text, cls }),
    () => out.push({ text: '\n', cls: '' }))
  return out
}

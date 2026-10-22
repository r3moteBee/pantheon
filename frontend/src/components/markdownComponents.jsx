import React from 'react'
import Mermaid from './Mermaid'
import CodeBlock, { nodeText } from './chat/CodeBlock'

// Fenced code: ```mermaid renders as a diagram; everything else as a
// CodeBlock (language label, copy button, syntax highlighting). With
// react-markdown 9 the <pre> wrapper is the only reliable block-vs-inline
// signal (the `inline` prop is gone), so block handling lives here and a
// call site's `code` override only styles inline code.
function PreOverride({ children, node, ...rest }) {
  const child = React.Children.toArray(children)[0]
  const className = child?.props?.className || ''
  const code = nodeText(child?.props?.children).replace(/\n$/, '')
  if (className.includes('language-mermaid')) return <Mermaid code={code} />
  if (child?.props) {
    const lang = (className.match(/language-([\w+#.-]+)/) || [])[1] || ''
    return <CodeBlock code={code} lang={lang} />
  }
  return <pre {...rest}>{children}</pre>
}

export const mermaidMarkdownComponents = {
  pre: PreOverride,
}

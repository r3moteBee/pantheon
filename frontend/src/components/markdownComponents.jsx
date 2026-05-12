import React from 'react'
import Mermaid from './Mermaid'

function PreOverride({ children, ...rest }) {
  const child = React.Children.toArray(children)[0]
  const className = child?.props?.className || ''
  if (className.includes('language-mermaid')) {
    const code = String(child.props.children || '').replace(/\n$/, '')
    return <Mermaid code={code} />
  }
  return <pre {...rest}>{children}</pre>
}

export const mermaidMarkdownComponents = {
  pre: PreOverride,
}

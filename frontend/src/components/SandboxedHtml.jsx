import React from 'react'
import { filesApi } from '../api/client'

/**
 * Render an untrusted workspace HTML file in an opaque-origin sandbox.
 *
 * The HTML is fetched with the normal Authorization header and passed via
 * srcDoc, so the frame never sees the auth token (a src= URL would carry it
 * in ?token=) and — without allow-same-origin — can't read localStorage or
 * call the API as the user.
 */
export default function SandboxedHtml({ path, projectId, title, className, style }) {
  const [html, setHtml] = React.useState(null)

  React.useEffect(() => {
    let cancelled = false
    setHtml(null)
    filesApi.read(path, projectId)
      .then((res) => { if (!cancelled) setHtml(res.data.content || '') })
      .catch(() => { if (!cancelled) setHtml('<p style="font-family:sans-serif">Failed to load file</p>') })
    return () => { cancelled = true }
  }, [path, projectId])

  if (html === null) {
    return <div className={className} style={style} />
  }
  return (
    <iframe
      srcDoc={html}
      title={title}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      className={className}
      style={style}
    />
  )
}

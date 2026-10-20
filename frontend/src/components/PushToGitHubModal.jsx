import { useEffect, useMemo, useState } from 'react'
import { X, Github, AlertTriangle, ExternalLink, Loader } from 'lucide-react'
import { connectionsApi, projectRepoApi, githubPushApi } from '../api/client'

/**
 * Push a folder (pathPrefix) or selected artifacts (artifactIds) to GitHub
 * as one commit (api/github_push.py). Default: a new branch + pull request.
 *
 * Props: projectId, pathPrefix?, artifactIds?, label, onClose
 */
export default function PushToGitHubModal({ projectId, pathPrefix, artifactIds, label, onClose }) {
  const name = (label || 'artifacts').split('/').filter(Boolean).pop() || 'artifacts'
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:]/g, '').replace('T', '-')
  const [connections, setConnections] = useState([])
  const [binding, setBinding] = useState(null)
  const [connId, setConnId] = useState('')
  const [repos, setRepos] = useState(null)
  const [repoChoice, setRepoChoice] = useState('')        // 'owner/repo' | '__new__'
  const [newRepo, setNewRepo] = useState({ name: name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-'), private: true })
  const [branch, setBranch] = useState(`pantheon/${name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-')}-${stamp}`)
  const [toDefault, setToDefault] = useState(false)
  const [destDir, setDestDir] = useState('')
  const [message, setMessage] = useState(`Update ${name} from Pantheon`)
  const [openPr, setOpenPr] = useState(true)
  const [mirror, setMirror] = useState(false)
  const [status, setStatus] = useState(null)
  const [sensitive, setSensitive] = useState(null)
  const [confirmSensitive, setConfirmSensitive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    (async () => {
      try {
        const [c, b] = await Promise.all([connectionsApi.list(), projectRepoApi.get(projectId)])
        const conns = c.data?.connections || []
        const bound = b.data?.binding || null
        setConnections(conns); setBinding(bound)
        setConnId(bound?.connection_id || conns[0]?.id || '')
        if (bound) setRepoChoice(`${bound.owner}/${bound.repo}`)
      } catch (e) { setError(e.message) }
      if (pathPrefix) {
        try { setStatus((await githubPushApi.pushes(projectId, pathPrefix)).data) } catch { /* optional */ }
      }
    })()
  }, [projectId, pathPrefix])

  useEffect(() => {
    if (!connId) return
    setRepos(null)
    connectionsApi.listConnectionRepos(connId)
      .then((r) => setRepos(r.data?.repos || []))
      .catch((e) => { setRepos([]); setError(e.message) })
  }, [connId])

  const repoInfo = useMemo(() => (repos || []).find((r) => r.full_name === repoChoice), [repos, repoChoice])
  const defaultBranch = repoInfo?.default_branch || (binding && `${binding.owner}/${binding.repo}` === repoChoice
    ? binding.default_branch : 'main')
  const lastRepos = useMemo(() => [...new Set((status?.items || [])
    .map((i) => i.last_push && `${i.last_push.repo}@${i.last_push.branch}`).filter(Boolean))], [status])

  const canPush = !busy && connId && repoChoice && (repoChoice !== '__new__' || newRepo.name.trim())
    && (toDefault || branch.trim()) && (!sensitive || confirmSensitive) && (!mirror || destDir.trim())

  const push = async () => {
    setBusy(true); setError(null)
    try {
      let owner, repo
      if (repoChoice === '__new__') {
        const r = await githubPushApi.createRepo(connId, { name: newRepo.name.trim(), private: newRepo.private })
        owner = r.data.owner; repo = r.data.repo
        setRepoChoice(r.data.full_name)
        setRepos((rs) => [{ full_name: r.data.full_name, default_branch: r.data.default_branch,
          private: r.data.private }, ...(rs || [])])
      } else {
        [owner, repo] = repoChoice.split('/')
      }
      const res = await githubPushApi.push(projectId, {
        path_prefix: pathPrefix || null, artifact_ids: artifactIds || null,
        connection_id: connId, owner, repo,
        branch: toDefault ? defaultBranch : branch.trim(), allow_default_branch: toDefault,
        dest_dir: destDir.trim(), message, open_pr: openPr && !toDefault, mirror,
        confirm_sensitive: confirmSensitive,
      })
      setResult(res.data)
    } catch (e) {
      const d = e.data?.detail
      if (e.status === 409 && d?.sensitive) { setSensitive(d.sensitive); setError(d.message) }
      else setError(e.message)
    } finally { setBusy(false) }
  }

  const field = 'w-full bg-gray-900 border border-gray-800 rounded px-2 py-1 text-xs'
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="w-full max-w-lg bg-gray-950 border border-gray-800 rounded-lg shadow-xl max-h-[90vh] overflow-y-auto"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <div className="flex items-center gap-2 text-sm font-semibold"><Github className="w-4 h-4" /> Push {name} to GitHub</div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200"><X className="w-4 h-4" /></button>
        </div>

        {result ? (
          <div className="p-4 space-y-3 text-xs">
            <p className="text-gray-200">{result.commit_sha
              ? `Pushed ${result.uploaded.length} new or changed file(s) to ${result.repo}@${result.branch}` +
                (result.unchanged.length ? `, ${result.unchanged.length} unchanged` : '') +
                (result.deleted.length ? `, ${result.deleted.length} deleted` : '') + '.'
              : `Nothing to commit: every file already matches ${result.repo}@${result.branch}.`}</p>
            {result.commit_url && <a href={result.commit_url} target="_blank" rel="noreferrer"
              className="flex items-center gap-1 text-brand-400 hover:underline"><ExternalLink className="w-3 h-3" /> View commit</a>}
            {result.pr_url && <a href={result.pr_url} target="_blank" rel="noreferrer"
              className="flex items-center gap-1 text-brand-400 hover:underline"><ExternalLink className="w-3 h-3" />
              Pull request #{result.pr_number}{result.pr_existing ? ' (already open)' : ''}</a>}
            <div className="flex justify-end"><button onClick={onClose} className="px-3 py-1 rounded bg-gray-800 hover:bg-gray-700">Done</button></div>
          </div>
        ) : (
          <div className="p-4 space-y-3 text-xs">
            {status && status.pushed > 0 && (
              <p className="text-gray-400">Last pushed to {lastRepos.join(', ')}; {status.changed} of {status.items.length} file(s) changed since.</p>
            )}
            {connections.length === 0 ? (
              <p className="text-gray-400">No GitHub connection yet. Add a token in Settings → Connections.</p>
            ) : (<>
              {connections.length > 1 && (
                <label className="block space-y-1"><span className="text-gray-400">Connection</span>
                  <select value={connId} onChange={(e) => { setConnId(e.target.value); setRepoChoice('') }} className={field}>
                    {connections.map((c) => <option key={c.id} value={c.id}>{c.account_login || c.display_name || c.id}</option>)}
                  </select>
                </label>
              )}
              <label className="block space-y-1"><span className="text-gray-400">Repository</span>
                <select value={repoChoice} onChange={(e) => setRepoChoice(e.target.value)} className={field}>
                  <option value="">{repos === null ? 'Loading repositories…' : 'Choose a repository'}</option>
                  <option value="__new__">+ Create a new repository</option>
                  {(repos || []).map((r) => <option key={r.full_name} value={r.full_name}>
                    {r.full_name}{r.private ? ' (private)' : ''}{binding && `${binding.owner}/${binding.repo}` === r.full_name ? ' — this project' : ''}
                  </option>)}
                </select>
              </label>
              {repoChoice === '__new__' && (
                <div className="flex items-center gap-2">
                  <input value={newRepo.name} onChange={(e) => setNewRepo({ ...newRepo, name: e.target.value })}
                         placeholder="repository name" className={field} />
                  <label className="flex items-center gap-1 whitespace-nowrap text-gray-300">
                    <input type="checkbox" checked={newRepo.private} onChange={(e) => setNewRepo({ ...newRepo, private: e.target.checked })} /> Private
                  </label>
                </div>
              )}
              <label className="block space-y-1"><span className="text-gray-400">Branch</span>
                <input value={toDefault ? defaultBranch : branch} disabled={toDefault}
                       onChange={(e) => setBranch(e.target.value)} className={field + (toDefault ? ' opacity-60' : '')} />
              </label>
              <label className="flex items-center gap-2 text-gray-300">
                <input type="checkbox" checked={toDefault} onChange={(e) => setToDefault(e.target.checked)} />
                Push straight to <code>{defaultBranch}</code> (no pull request)
              </label>
              <label className="block space-y-1"><span className="text-gray-400">Folder in the repository (empty = root)</span>
                <input value={destDir} onChange={(e) => setDestDir(e.target.value)} placeholder="e.g. apps/spider" className={field} />
              </label>
              <label className="block space-y-1"><span className="text-gray-400">Commit message</span>
                <input value={message} onChange={(e) => setMessage(e.target.value)} className={field} />
              </label>
              {!toDefault && (
                <label className="flex items-center gap-2 text-gray-300">
                  <input type="checkbox" checked={openPr} onChange={(e) => setOpenPr(e.target.checked)} /> Open a pull request
                </label>
              )}
              <label className="flex items-start gap-2 text-gray-300">
                <input type="checkbox" className="mt-0.5" checked={mirror} onChange={(e) => setMirror(e.target.checked)} />
                <span>Delete files in that repository folder that aren't in this push
                  {mirror && !destDir.trim() && <span className="block text-amber-400">Needs a folder in the repository.</span>}</span>
              </label>
              {sensitive && (
                <div className="rounded border border-amber-700 bg-amber-950/40 p-2 space-y-1">
                  <div className="flex items-center gap-1 text-amber-300"><AlertTriangle className="w-3 h-3" /> These look like they contain credentials:</div>
                  <ul className="list-disc pl-5 text-amber-200">{sensitive.map((p) => <li key={p}>{p}</li>)}</ul>
                  <label className="flex items-center gap-2 text-amber-200">
                    <input type="checkbox" checked={confirmSensitive} onChange={(e) => setConfirmSensitive(e.target.checked)} /> Push them anyway
                  </label>
                </div>
              )}
            </>)}
            {error && !sensitive && <p className="text-red-400">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={onClose} className="px-3 py-1 rounded bg-gray-800 hover:bg-gray-700">Cancel</button>
              <button onClick={push} disabled={!canPush}
                      className="px-3 py-1 rounded bg-brand-600 hover:bg-brand-500 disabled:opacity-50 flex items-center gap-1">
                {busy && <Loader className="w-3 h-3 animate-spin" />} {repoChoice === '__new__' ? 'Create and push' : 'Push'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

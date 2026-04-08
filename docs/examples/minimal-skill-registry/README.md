# Minimal Pantheon Skill Registry

A ~150-line FastAPI reference implementation of the
[Pantheon Skill Registry Protocol](../../skill-registry-protocol.md) v1.0.

Serves a single example skill (`acme/jql-helper`) bundled in-memory as a
tar.gz, so platform teams can see the exact shapes Pantheon expects before
adapting this to their own internal catalog.

## Run

```bash
pip install fastapi uvicorn
uvicorn server:app --reload --port 8788
```

Then in Pantheon: **Settings → Skills → Hubs → Add Hub** and enter
`http://localhost:8788`. The new registry will appear in the importer's
hub dropdown alongside GitHub.

## What to change for your deployment

1. Replace the `SKILLS` dict and `_build_bundle()` with a query against
   your real catalog (artifact store, git repo, database).
2. Switch `auth.type` in the discovery doc to `bearer` or `mtls` and
   enforce it in a FastAPI dependency.
3. Add HTTPS via your normal reverse proxy.
4. If you sign bundles, emit `trust.signature` as a Sigstore bundle and
   set `capabilities.signing: "sigstore"` in the discovery document.
5. Implement cursor-based pagination once your catalog grows past a few
   hundred entries.

## Conformance

See the checklist in
[`../../skill-registry-protocol.md`](../../skill-registry-protocol.md) §10.

# Deploying the public demo to Vercel

The demo is a static UI (`public/`) plus one Python function (`api/diff.py`, `POST /api/diff`).
Deployment uses Vercel's Git integration: a push to the production branch deploys.

## One-time setup

1. In Vercel choose **Add New... > Project** and **import** this Git repository.
2. Framework Preset: Other. Leave Build Command and Install Command empty; the output
   directory comes from `vercel.json` (`public`).
3. Settings > Git > **Production Branch**: set it to `main` (other branches get preview deploys).
4. Deploy. `vercel.json` bundles `src/**` into the function (`includeFiles`) and routes
   `/api/diff` to `api/diff.py`. The root `requirements.txt` holds only `pydantic`.

## Before pushing

```
python scripts/check_demo_size.py     # fails above 50 MB
```

## Verify after the first deploy

Vercel's runtime cannot be exercised locally, so check by hand on the deployment URL:

1. `/` loads the UI and the examples picker works; no 404s for `app.js`, `style.css`, `examples.json`.
2. Pick an example and press Diff: the result shows the verdict and per-change badges.
3. `curl -X POST https://<deployment>/api/diff -H 'content-type: application/json' -d '{"baseline":<snapshot>,"current":<snapshot>}'`
   returns 200 with a DriftReport.
4. `curl -X POST https://<deployment>/api/diff -d 'nope'` returns 400 with a readable error.
5. A body over 256 KB returns 400.
6. Function logs (Vercel > Logs) show no request bodies and no `ModuleNotFoundError`
   (if `schemasentinel` is missing, `includeFiles` is not taking effect).
7. Replace the live demo URL placeholder in the README with the real one.

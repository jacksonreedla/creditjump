# Deploying CreditJump

Two pieces: the **API** (this folder) and the **front end** (`index.html`).

## 1. Put the code on GitHub
```
git init && git add . && git commit -m "CreditJump v0"
# create a repo on github.com, then:
git remote add origin https://github.com/YOU/creditjump.git
git push -u origin main
```

## 2. Deploy the API

### Option A — Render (uses render.yaml, easiest)
1. render.com → New → Blueprint → pick your repo.
2. It reads `render.yaml` automatically. Set `ALLOWED_ORIGINS` to your
   front-end domain once you have it (step 3).
3. You get a URL like `https://creditjump-api.onrender.com`.
> Free tier sleeps when idle — the first request after a quiet spell takes
> ~30s to wake. The front end shows a "first load is slow" message for this.

### Option B — Railway
New Project → Deploy from repo. Railway reads the `Procfile`. Add the same
env vars under Variables. 

### Option C — Fly.io (uses Dockerfile)
```
fly launch --no-deploy        # generates fly.toml from the Dockerfile
fly secrets set ALLOWED_ORIGINS=https://your-domain
fly deploy
```

Confirm it's live: open `https://YOUR-API-URL/health` → `{"status":"ok"}`.
Interactive docs live at `/docs`.

## 3. Deploy the front end
1. In `index.html`, set `API_BASE` (top of the script) to your API URL.
2. Host the file free on **Netlify**, **Vercel**, or **Cloudflare Pages**
   (drag-and-drop the file, or connect the repo). HTTPS is automatic.
3. Copy the resulting domain and put it in the API's `ALLOWED_ORIGINS`
   env var, then redeploy the API so CORS allows your site.

## 4. Custom domain
Buy a domain (Namecheap/Cloudflare ~$10/yr) and point it at the front-end
host. Update `ALLOWED_ORIGINS` to match.

## Adding more schools
Drop a new file in `articulations/` shaped like the existing one (a
`destination` name + `equivalencies` rows). It appears in `/schools`
automatically on the next deploy — no code change.

## Before you take real traffic
- [ ] `ALLOWED_ORIGINS` set to your domain (not `*`)
- [ ] Privacy + Terms pages linked in the footer
- [ ] Analytics installed (Plausible / GA4)
- [ ] The "unofficial estimate" disclaimer is visible on results

# Universal Media Saver — Railway + Frontend Test Setup

This starter uses the official Cobalt v11 container as the processing backend and a small static frontend for testing public/authorized media links.

## Project structure

- `backend-railway/` — Railway-ready Docker wrapper around `ghcr.io/imputnet/cobalt:11`
- `frontend/` — static HTML/CSS/JS test website; can be hosted on Netlify, Cloudflare Pages, GitHub Pages, or another static host

## 1) Deploy backend to Railway

1. Create a new GitHub repository and upload the contents of `backend-railway/` to its root, OR keep this monorepo and set Railway Root Directory to `/backend-railway`.
2. Railway → New Project → Deploy from GitHub Repo.
3. Railway detects `Dockerfile` automatically.
4. In the service `Variables` tab add:

```env
API_PORT=9000
CORS_WILDCARD=1
RATELIMIT_WINDOW=60
RATELIMIT_MAX=30
SESSION_RATELIMIT_WINDOW=60
SESSION_RATELIMIT_MAX=15
TUNNEL_RATELIMIT_WINDOW=60
TUNNEL_RATELIMIT_MAX=60
DISABLED_SERVICES=youtube
```

5. Railway → Settings/Networking → Generate Domain.
6. Copy the generated public URL, e.g. `https://your-service.up.railway.app`.
7. Add/update this required variable:

```env
API_URL=https://your-service.up.railway.app/
```

8. Redeploy.
9. Open the Railway URL in a browser. A JSON server-info response means the API is alive.

> Important: `API_URL` is required by Cobalt. It must be the final externally reachable Railway URL and should end with `/`.

## 2) Connect frontend

Edit:

`frontend/config.js`

and replace:

```js
apiBaseUrl: "https://YOUR-SERVICE.up.railway.app/"
```

with your real Railway backend URL.

## 3) Run frontend quickly

You can open it through any static server. For example, from the `frontend` directory:

```bash
python -m http.server 8080
```

Then visit `http://localhost:8080`.

Do not test by double-clicking `index.html` if your browser blocks some network/CORS behavior for `file://` pages.

## 4) Deploy frontend to Netlify

- Create a new Netlify site from GitHub.
- Set Base directory to `frontend` if using this monorepo.
- Publish directory: `.`
- No build command is required.

Because the test backend currently uses `CORS_WILDCARD=1`, the static frontend can call it directly.

## API behavior used by frontend

The current Cobalt API processing endpoint is:

```http
POST /
Accept: application/json
Content-Type: application/json
```

Example request:

```json
{
  "url": "https://example.com/public-media-link",
  "videoQuality": "1080",
  "downloadMode": "auto",
  "filenameStyle": "pretty",
  "alwaysProxy": false
}
```

The frontend handles these Cobalt response statuses:

- `redirect` — direct source link
- `tunnel` — Cobalt-served download link
- `picker` — multiple media items (carousel/gallery)
- `error` — readable error message

## Test order

Recommended first tests:

1. X public video post
2. Reddit public video/image post
3. Vimeo public video
4. Pinterest public media
5. Instagram public Reel/post
6. Facebook public video

Some services may require cookies/authentication or can break when the upstream platform changes. Do not use this project to bypass private-account access, DRM, paywalls, or access controls.

## Before public release

For testing, `CORS_WILDCARD=1` is convenient. For a public production service:

- Restrict CORS to your website origin
- Add Cobalt API-key or Turnstile protection
- Add abuse/rate-limit controls
- Monitor bandwidth because `tunnel` responses may send media through the Railway service
- Review each supported platform's terms and Google Play policy
- Keep `youtube` disabled in the Play-Store-oriented build unless you have a compliant use case

## Useful Railway note

Railway can build a root `Dockerfile` automatically. Variables are configured in the service Variables tab, and the public domain is generated under Networking.

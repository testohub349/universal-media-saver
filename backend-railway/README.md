# Universal Media Saver Backend v2

This backend replaces the Cobalt-only test backend with FastAPI + yt-dlp.

## Endpoints

- `GET /health`
- `GET /`
- `POST /` - Cobalt-like compatibility endpoint for the existing frontend
- `POST /extract` - richer JSON response

### Request

```json
{
  "url": "https://www.facebook.com/reel/...",
  "videoQuality": "max",
  "downloadMode": "auto"
}
```

## Railway deployment

Create a NEW Railway service from this folder/repository.

Variables:

```env
PORT=9000
```

Set Public Networking target port to `9000`.

Healthcheck path:

```text
/health
```

## Important limitation

Facebook, Instagram and similar platforms increasingly require login/cookies or block datacenter IP addresses.
No open-source extractor can guarantee every public link will work forever.

If a URL fails with a login/cookie message, add a Netscape `cookies.txt` file encoded as base64:

macOS:

```bash
base64 -i cookies.txt | pbcopy
```

Then create Railway variable:

```env
COOKIES_B64=<paste-base64-here>
```

Redeploy.

Do not commit cookies to GitHub.

## Test from Mac

```bash
curl -s -X POST 'https://YOUR-RAILWAY-DOMAIN/' \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  --data '{"url":"https://www.dailymotion.com/video/xa6x0vw","videoQuality":"max"}'
```

Use plain URLs in Terminal, not Markdown `[url](url)` syntax.

## Existing Netlify frontend

The compatibility endpoint is `POST /`, so the existing fixed frontend can use this backend by changing only `config.js`:

```js
window.APP_CONFIG = {
  apiBaseUrl: "https://YOUR-NEW-RAILWAY-DOMAIN/"
};
```

## Why this is better for debugging

The `/extract` endpoint returns:
- extractor/service name
- title
- thumbnail
- direct media URL
- dimensions
- approximate size
- required HTTP headers

This makes platform failures visible instead of hiding them behind a generic 400 error.

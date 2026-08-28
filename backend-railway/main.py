import os
import re
import html
import json
import base64
import tempfile
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse, urlunparse, quote
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import yt_dlp

app = FastAPI(title="Universal Media Saver API", version="2.4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])

COOKIE_FILE = None
MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 15; SM-A065F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
DESKTOP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def prepare_cookie_file():
    global COOKIE_FILE
    raw = os.getenv("COOKIES_B64", "").strip()
    if not raw:
        return
    try:
        data = base64.b64decode(raw)
        fd, path = tempfile.mkstemp(prefix="ums_cookies_", suffix=".txt")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(data)
        COOKIE_FILE = path
    except Exception:
        COOKIE_FILE = None
prepare_cookie_file()

class ExtractRequest(BaseModel):
    url: str
    videoQuality: Optional[str] = "max"
    downloadMode: Optional[str] = "auto"
    filenameStyle: Optional[str] = "pretty"

def host_is(host: str, domain: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return host == domain or host.endswith("." + domain)

def is_facebook_host(h): return (h or "").lower().split(":")[0] == "fb.watch" or host_is(h, "facebook.com")
def is_tiktok_host(h): return host_is(h, "tiktok.com")
def is_reddit_host(h): return host_is(h, "reddit.com") or host_is(h, "redd.it")
def is_snapchat_host(h): return host_is(h, "snapchat.com")

def normalize_input_url(raw: str) -> str:
    s = (raw or "").strip().replace("\u200b", "")
    md = re.search(r"\[[^\]]*\](https?://[^)\s]+)\)", s, re.I)
    if md:
        s = md.group(1)
    else:
        found = re.search(r"https?://[^\s)]}>\"\\']+", s, re.I)
        if found:
            s = found.group(0)
    while s.endswith((".", ",", ";", ")")):
        s = s[:-1]
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s.lstrip("/")
    return s

def follow_redirect(url: str, headers: Dict[str, str], timeout: int = 15) -> str:
    req = UrlRequest(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return resp.geturl()

def clean_tiktok_url(url: str) -> str:
    try:
        p = urlparse(url)
        if is_tiktok_host(p.netloc) and "/video/" in (p.path or ""):
            return urlunparse((p.scheme or "https", p.netloc, p.path, "", "", ""))
    except Exception:
        pass
    return url

def resolve_tiktok_url(url: str) -> str:
    try:
        p = urlparse(url)
        if (p.netloc or "").lower().split(":")[0] in {"vm.tiktok.com", "vt.tiktok.com"}:
            return clean_tiktok_url(follow_redirect(url, DESKTOP_HEADERS))
        return clean_tiktok_url(url)
    except Exception:
        return url

def resolve_reddit_url(url: str) -> str:
    try:
        p = urlparse(url)
        path = p.path or ""
        short_share = bool(re.search(r"/r/[^/]+/s/[^/]+/?$", path, re.I)) or host_is(p.netloc, "redd.it")
        if short_share:
            h = dict(DESKTOP_HEADERS); h["Referer"] = "https://www.reddit.com/"
            final = follow_redirect(url, h)
            if is_reddit_host(urlparse(final).netloc):
                return final.split("?")[0]
    except Exception:
        pass
    return url

def clean_facebook_url(url: str) -> str:
    try:
        p = urlparse(url); path = (p.path or "").lower()
        if "/watch" in path and "v=" in (p.query or ""):
            keep = [x for x in p.query.split("&") if x.startswith("v=")]
            return urlunparse((p.scheme or "https", p.netloc, p.path, p.params, "&".join(keep), ""))
        if any(x in path for x in ("/reel/", "/reels/", "/videos/")):
            return urlunparse((p.scheme or "https", p.netloc, p.path, p.params, "", ""))
    except Exception:
        pass
    return url

def looks_like_facebook_media_url(url: str) -> bool:
    try:
        p = urlparse(url); path = (p.path or "").lower()
        return is_facebook_host(p.netloc) and (any(x in path for x in ("/reel/", "/reels/", "/videos/", "/watch")) or "v=" in (p.query or ""))
    except Exception:
        return False

def resolve_facebook_share_url(url: str) -> str:
    try:
        p = urlparse(url); path = (p.path or "").lower(); host = (p.netloc or "").lower()
        if not (host == "fb.watch" or "/share/" in path or path.startswith("/share")):
            return clean_facebook_url(url)
        req = UrlRequest(url, headers=MOBILE_HEADERS, method="GET")
        with urlopen(req, timeout=15) as resp:
            final = resp.geturl()
            if looks_like_facebook_media_url(final):
                return clean_facebook_url(final)
            body = resp.read(512 * 1024).decode("utf-8", errors="ignore")
            for pat in (r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^\"\\']+)',
                        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^\"\\']+)'):
                m = re.search(pat, body, re.I)
                if m:
                    candidate = html.unescape(m.group(1)).replace("\\/", "/")
                    if looks_like_facebook_media_url(candidate):
                        return clean_facebook_url(candidate)
    except Exception:
        pass
    return url

def prepare_target_url(raw: str) -> str:
    url = normalize_input_url(raw)
    try:
        host = urlparse(url).netloc
        if is_facebook_host(host): return resolve_facebook_share_url(url)
        if is_tiktok_host(host): return resolve_tiktok_url(url)
        if is_reddit_host(host): return resolve_reddit_url(url)
    except Exception:
        pass
    return url

def quality_height(q: Optional[str]) -> Optional[int]:
    try:
        return None if not q or q == "max" else int(q)
    except Exception:
        return None

def ydl_opts(req: ExtractRequest, target_url: str, attempt: int = 0) -> Dict[str, Any]:
    h = quality_height(req.videoQuality); mode = (req.downloadMode or "auto").lower()
    if mode == "audio": fmt = "bestaudio/best"
    elif mode == "mute": fmt = f"bestvideo[height<={h}]" if h else "bestvideo"
    elif h: fmt = f"best[height<={h}]/bestvideo[height<={h}]+bestaudio/best"
    else: fmt = "best/bestvideo+bestaudio"
    host = urlparse(target_url).netloc
    special = is_tiktok_host(host) or is_reddit_host(host) or is_snapchat_host(host)
    opts = {
        "quiet": True, "no_warnings": False, "skip_download": True, "format": fmt,
        "noplaylist": False, "extract_flat": False, "nocheckcertificate": True,
        "socket_timeout": 30, "retries": 3, "fragment_retries": 3,
        "http_headers": {"User-Agent": DESKTOP_HEADERS["User-Agent"] if special else MOBILE_HEADERS["User-Agent"],
                         "Accept-Language": "en-US,en;q=0.9"},
    }
    if is_tiktok_host(host):
        opts["http_headers"]["Referer"] = "https://www.tiktok.com/"
        if attempt == 1:
            opts["impersonate"] = "chrome"
        elif attempt == 2:
            opts["http_headers"]["User-Agent"] = MOBILE_HEADERS["User-Agent"]
    elif is_reddit_host(host):
        opts["http_headers"]["Referer"] = "https://www.reddit.com/"
    elif is_snapchat_host(host):
        opts["http_headers"]["Referer"] = "https://www.snapchat.com/"
    if COOKIE_FILE:
        opts["cookiefile"] = COOKIE_FILE
    return opts

def safe_filename(info: Dict[str, Any]) -> str:
    title = (info.get("title") or "media").strip()
    for ch in '<>:"/\\|?*': title = title.replace(ch, "_")
    title = " ".join(title.split())[:120]
    return f"{title}.{info.get('ext') or 'mp4'}"

def format_score(f: Dict[str, Any]) -> Tuple:
    text = " ".join(str(f.get(k) or "") for k in ("format_id", "format", "format_note", "url")).lower()
    watermark_penalty = -100000 if any(x in text for x in ("watermark", "watermarked")) else 0
    clean_bonus = 50000 if any(x in text for x in ("nowm", "no_watermark", "original", "source", "play_addr", "playaddr")) else 0
    if "download_addr" in text or "downloadaddr" in text:
        watermark_penalty -= 40000
    combined = 1 if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none") else 0
    return (watermark_penalty + clean_bonus, combined, f.get("height") or 0, f.get("tbr") or 0, f.get("filesize") or f.get("filesize_approx") or 0)

def pick_direct(info: Dict[str, Any]):
    formats = [f for f in (info.get("formats") or []) if f and f.get("url")]
    if formats:
        best = max(formats, key=format_score)
        return best.get("url"), best.get("http_headers") or info.get("http_headers") or {}
    requested = info.get("requested_downloads") or info.get("requested_formats") or []
    if isinstance(requested, list):
        candidates = [x for x in requested if isinstance(x, dict) and x.get("url")]
        if candidates:
            best = max(candidates, key=format_score)
            return best.get("url"), best.get("http_headers") or info.get("http_headers") or {}
    if info.get("url"):
        return info.get("url"), info.get("http_headers") or {}
    return None, {}

def reddit_json_fallback(target_url: str) -> Optional[Dict[str, Any]]:
    try:
        p = urlparse(target_url)
        if not is_reddit_host(p.netloc): return None
        path = (p.path or "").rstrip("/")
        if "/comments/" not in path: return None
        api_url = f"https://www.reddit.com{path}.json?raw_json=1"
        h = dict(DESKTOP_HEADERS); h["Referer"] = "https://www.reddit.com/"
        req = UrlRequest(api_url, headers=h, method="GET")
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read(2 * 1024 * 1024).decode("utf-8", errors="ignore"))
        post = data[0]["data"]["children"][0]["data"]
        rv = ((post.get("secure_media") or {}).get("reddit_video") or (post.get("media") or {}).get("reddit_video") or {})
        url = rv.get("hls_url") or rv.get("fallback_url")
        if url:
            return {"id": post.get("id"), "title": post.get("title") or "reddit_media", "ext": "mp4",
                    "url": html.unescape(url), "http_headers": h, "_ums_resolved_url": target_url, "extractor": "reddit-json"}
        preview = post.get("preview") or {}
        images = preview.get("images") or []
        if images:
            src = ((images[0].get("source") or {}).get("url"))
            if src:
                return {"id": post.get("id"), "title": post.get("title") or "reddit_image", "ext": "jpg",
                        "url": html.unescape(src), "http_headers": h, "_ums_resolved_url": target_url, "extractor": "reddit-json"}
    except Exception:
        return None
    return None

def extract(req: ExtractRequest) -> Dict[str, Any]:
    target_url = prepare_target_url(req.url)
    attempts = 3 if is_tiktok_host(urlparse(target_url).netloc) else 1
    errors: List[str] = []
    for attempt in range(attempts):
        try:
            with yt_dlp.YoutubeDL(ydl_opts(req, target_url, attempt)) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if info:
                    if isinstance(info, dict):
                        info["_ums_input_url"] = req.url
                        info["_ums_resolved_url"] = target_url
                    return info
        except Exception as e:
            errors.append(str(e) or repr(e) or e.__class__.__name__)
    if is_reddit_host(urlparse(target_url).netloc):
        fb = reddit_json_fallback(target_url)
        if fb:
            return fb
    msg = " | ".join(dict.fromkeys(errors)) or "Extractor returned no media data"
    raise HTTPException(status_code=422, detail={"code": "extract.failed", "message": msg,
        "hint": "Public media is supported. TikTok retries multiple browser profiles; Reddit share links also use a JSON media fallback.",
        "resolved_url": target_url})

def is_tiktok_cdn_url(url: str) -> bool:
    """Check if URL is a TikTok CDN domain that needs proxying."""
    try:
        host = (urlparse(url).netloc or "").lower()
        return any(x in host for x in ("tiktok", "tstatic.net", "akamaized.net", "douyin"))
    except Exception:
        return False

def build_result(info: Dict[str, Any]):
    entries = info.get("entries")
    if entries:
        picker = []
        for x in entries:
            if not x: continue
            direct, headers = pick_direct(x)
            if direct:
                picker.append({"type": "video" if x.get("vcodec") not in (None, "none") else "photo",
                               "url": direct, "thumb": x.get("thumbnail"), "filename": safe_filename(x), "headers": headers})
        if picker:
            return {"status": "picker", "picker": picker, "resolvedUrl": info.get("_ums_resolved_url")}
    direct, headers = pick_direct(info)
    if not direct:
        raise HTTPException(status_code=422, detail={"code": "extract.empty", "message": "No direct media URL was found.",
                                                           "resolved_url": info.get("_ums_resolved_url")})
    
    # For TikTok CDN URLs, use proxy endpoint instead of raw CDN
    if is_tiktok_cdn_url(direct):
        proxy_url = f"/download?url={quote(direct, safe='')}"
        return {"status": "redirect", "url": proxy_url, "filename": safe_filename(info), "headers": headers,
                "resolvedUrl": info.get("_ums_resolved_url")}
    
    return {"status": "redirect", "url": direct, "filename": safe_filename(info), "headers": headers,
            "resolvedUrl": info.get("_ums_resolved_url")}

def proxy_headers_for_url(url: str) -> Dict[str, str]:
    """Get headers to use when proxying a request to a media URL."""
    headers = {}
    if is_tiktok_cdn_url(url):
        headers["User-Agent"] = MOBILE_HEADERS["User-Agent"]
        headers["Referer"] = "https://www.tiktok.com/"
    return headers

@app.get("/")
def root():
    return {"status": "ok", "name": "Universal Media Saver API", "version": "2.4.0", "engine": "yt-dlp",
            "facebook_share_resolver": True, "tiktok_shortlink_resolver": True, "reddit_share_resolver": True,
            "reddit_json_fallback": True, "prefer_clean_source_stream": True, "cookies_loaded": bool(COOKIE_FILE)}

@app.get("/health")
def health(): return {"status": "ok", "version": "2.4.0"}

@app.get("/download")
def download_proxy(url: str, range_header: Optional[str] = Header(None)):
    """Proxy endpoint for downloading media files with proper headers and Range support."""
    if not url:
        raise HTTPException(status_code=400, detail="url parameter is required")
    
    try:
        # Validate URL is a remote media URL
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Invalid URL")
        
        # Prepare headers for the upstream request
        upstream_headers = proxy_headers_for_url(url)
        upstream_headers["Accept"] = "video/mp4,video/*,*/*;q=0.9"
        upstream_headers["Accept-Encoding"] = "gzip, deflate"
        
        # Add Range header if provided by client
        if range_header:
            upstream_headers["Range"] = range_header
        
        # Open connection to upstream
        req = UrlRequest(url, headers=upstream_headers, method="GET")
        resp = urlopen(req, timeout=30)
        
        # Extract response metadata
        content_type = resp.headers.get("Content-Type", "video/mp4")
        content_length = resp.headers.get("Content-Length")
        accept_ranges = resp.headers.get("Accept-Ranges", "bytes")
        content_range = resp.headers.get("Content-Range")
        status_code = resp.getcode()
        
        # Prepare response headers
        response_headers = {
            "Content-Type": content_type,
            "Accept-Ranges": accept_ranges,
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type",
            "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges",
        }
        
        if content_length:
            response_headers["Content-Length"] = content_length
        if content_range:
            response_headers["Content-Range"] = content_range
        
        # Set Content-Disposition for downloads
        filename = "video.mp4"
        try:
            disposition = resp.headers.get("Content-Disposition", "")
            if disposition:
                response_headers["Content-Disposition"] = disposition
            else:
                response_headers["Content-Disposition"] = f"attachment; filename={filename}"
        except Exception:
            response_headers["Content-Disposition"] = f"attachment; filename={filename}"
        
        # Stream the response
        def iter_content(chunk_size: int = 8192):
            try:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                resp.close()
        
        return StreamingResponse(
            iter_content(),
            status_code=status_code,
            headers=response_headers,
            media_type=content_type
        )
    
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to proxy media: {str(e)}")

@app.post("/extract")
def extract_rich(req: ExtractRequest):
    info = extract(req)
    result = build_result(info)
    if result["status"] == "picker":
        return {"status": "picker", "resolved_url": result.get("resolvedUrl"), "items": result["picker"]}
    return {"status": "ok", "service": info.get("extractor_key") or info.get("extractor"),
            "filename": result["filename"], "resolved_url": result.get("resolvedUrl"),
            "media": {"url": result["url"], "http_headers": result["headers"], "thumbnail": info.get("thumbnail")}}

@app.post("/")
def cobalt_compatible(req: ExtractRequest):
    try:
        return build_result(extract(req))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"status": "error", "error": {
            "code": detail.get("code", "extract.failed"), "message": detail.get("message", "Extraction failed"),
            "hint": detail.get("hint"), "resolvedUrl": detail.get("resolved_url"), "engine": "yt-dlp"}})


import os
import re
import html
import base64
import tempfile
from typing import Optional, Dict, Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import yt_dlp

app = FastAPI(title="Universal Media Saver API", version="2.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

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


def normalize_input_url(raw: str) -> str:
    s = (raw or "").strip().replace("\u200b", "")
    md = re.search(r"\[[^\]]*\]\((https?://[^\)\s]+)\)", s, re.I)
    if md:
        s = md.group(1)
    else:
        found = re.search(r"https?://[^\s)>\]}>\"']+", s, re.I)
        if found:
            s = found.group(0)
    while s.endswith((".", ",", ";", ")")):
        s = s[:-1]
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s.lstrip("/")
    return s


def host_is(host: str, domain: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return host == domain or host.endswith("." + domain)


def is_facebook_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return host == "fb.watch" or host_is(host, "facebook.com")


def is_tiktok_host(host: str) -> bool:
    return host_is(host, "tiktok.com")


def is_reddit_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return host_is(host, "reddit.com") or host == "redd.it" or host.endswith(".redd.it")


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
        host = (p.netloc or "").lower().split(":")[0]
        if host in {"vm.tiktok.com", "vt.tiktok.com"}:
            return clean_tiktok_url(follow_redirect(url, DESKTOP_HEADERS))
        return clean_tiktok_url(url)
    except Exception:
        return url


def resolve_reddit_url(url: str) -> str:
    try:
        p = urlparse(url)
        path = p.path or ""
        short_share = bool(re.search(r"/r/[^/]+/s/[^/]+/?$", path, re.I)) or p.netloc.lower() == "redd.it"
        if not short_share:
            return url
        headers = dict(DESKTOP_HEADERS)
        headers["Referer"] = "https://www.reddit.com/"
        final_url = follow_redirect(url, headers)
        if is_reddit_host(urlparse(final_url).netloc):
            return final_url
    except Exception:
        pass
    return url


def looks_like_facebook_media_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if not is_facebook_host(p.netloc):
            return False
        path = (p.path or "").lower()
        return any(x in path for x in ("/reel/", "/reels/", "/videos/", "/watch")) or "v=" in (p.query or "")
    except Exception:
        return False


def clean_facebook_url(url: str) -> str:
    try:
        p = urlparse(url)
        path = (p.path or "").lower()
        if "/watch" in path and "v=" in (p.query or ""):
            keep = [x for x in p.query.split("&") if x.startswith("v=")]
            return urlunparse((p.scheme or "https", p.netloc, p.path, p.params, "&".join(keep), ""))
        if any(x in path for x in ("/reel/", "/reels/", "/videos/")):
            return urlunparse((p.scheme or "https", p.netloc, p.path, p.params, "", ""))
    except Exception:
        pass
    return url


def resolve_facebook_share_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower()
        path = (p.path or "").lower()
        needs_resolution = host == "fb.watch" or "/share/" in path or path.startswith("/share")
        if not needs_resolution:
            return clean_facebook_url(url)
        req = UrlRequest(url, headers=MOBILE_HEADERS, method="GET")
        with urlopen(req, timeout=15) as resp:
            final_url = resp.geturl()
            if looks_like_facebook_media_url(final_url):
                return clean_facebook_url(final_url)
            body = resp.read(512 * 1024).decode("utf-8", errors="ignore")
            for pat in (
                r'<meta[^>]+property=["\\']og:url["\\'][^>]+content=["\\']([^\"\\']+)',
                r'<link[^>]+rel=["\\']canonical["\\'][^>]+href=["\\']([^\"\\']+)',
            ):
                m = re.search(pat, body, re.I)
                if m:
                    candidate = html.unescape(m.group(1)).replace("\\\\/", "/")
                    if looks_like_facebook_media_url(candidate):
                        return clean_facebook_url(candidate)
    except Exception:
        pass
    return url


def prepare_target_url(raw: str) -> str:
    url = normalize_input_url(raw)
    try:
        host = urlparse(url).netloc
        if is_facebook_host(host):
            return resolve_facebook_share_url(url)
        if is_tiktok_host(host):
            return resolve_tiktok_url(url)
        if is_reddit_host(host):
            return resolve_reddit_url(url)
    except Exception:
        pass
    return url


def quality_height(q: Optional[str]) -> Optional[int]:
    if not q or q == "max":
        return None
    try:
        return int(q)
    except Exception:
        return None


def ydl_opts(req: ExtractRequest, target_url: str) -> Dict[str, Any]:
    h = quality_height(req.videoQuality)
    mode = (req.downloadMode or "auto").lower()
    if mode == "audio":
        fmt = "bestaudio/best"
    elif mode == "mute":
        fmt = f"bestvideo[height<={h}]" if h else "bestvideo"
    elif h:
        fmt = f"best[height<={h}]/bestvideo[height<={h}]+bestaudio/best"
    else:
        fmt = "best/bestvideo+bestaudio"

    host = urlparse(target_url).netloc
    tiktok = is_tiktok_host(host)
    reddit = is_reddit_host(host)
    opts = {
        "quiet": True,
        "no_warnings": False,
        "skip_download": True,
        "format": fmt,
        "noplaylist": False,
        "extract_flat": False,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "User-Agent": DESKTOP_HEADERS["User-Agent"] if (tiktok or reddit) else MOBILE_HEADERS["User-Agent"],
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if tiktok:
        opts["http_headers"]["Referer"] = "https://www.tiktok.com/"
    elif reddit:
        opts["http_headers"]["Referer"] = "https://www.reddit.com/"
    if COOKIE_FILE:
        opts["cookiefile"] = COOKIE_FILE
    return opts


def safe_filename(info: Dict[str, Any]) -> str:
    title = (info.get("title") or "media").strip()
    for ch in '<>:"/\\|?*':
        title = title.replace(ch, "_")
    title = " ".join(title.split())[:120]
    return f"{title}.{info.get('ext') or 'mp4'}"


def pick_direct(info: Dict[str, Any]):
    if info.get("url"):
        return info.get("url"), info.get("http_headers") or {}

    requested = info.get("requested_downloads") or []
    if len(requested) == 1 and requested[0].get("url"):
        return requested[0].get("url"), requested[0].get("http_headers") or info.get("http_headers") or {}

    formats = info.get("formats") or []
    combined = [f for f in formats if f.get("url") and f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
    if combined:
        best = max(combined, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        return best.get("url"), best.get("http_headers") or info.get("http_headers") or {}

    any_format = [f for f in formats if f.get("url")]
    if any_format:
        best = max(any_format, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        return best.get("url"), best.get("http_headers") or info.get("http_headers") or {}
    return None, {}


def extract(req: ExtractRequest) -> Dict[str, Any]:
    target_url = prepare_target_url(req.url)
    try:
        with yt_dlp.YoutubeDL(ydl_opts(req, target_url)) as ydl:
            info = ydl.extract_info(target_url, download=False)
            if not info:
                raise RuntimeError("Extractor returned no media data")
            if isinstance(info, dict):
                info["_ums_input_url"] = req.url
                info["_ums_resolved_url"] = target_url
            return info
    except yt_dlp.utils.DownloadError as e:
        msg = str(e) or repr(e) or e.__class__.__name__
        raise HTTPException(status_code=422, detail={
            "code": "extract.failed",
            "message": msg,
            "hint": "Public media is supported. TikTok uses browser impersonation; Reddit share links are resolved automatically. Private, removed, region-restricted or login-only media can still fail.",
            "resolved_url": target_url,
        })
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e) or repr(e) or e.__class__.__name__
        raise HTTPException(status_code=500, detail={
            "code": "extract.internal",
            "message": msg,
            "hint": "Internal extractor/networking error. The server now returns the exception type instead of a blank message.",
            "resolved_url": target_url,
        })


@app.get("/")
def root():
    return {
        "status": "ok",
        "name": "Universal Media Saver API",
        "version": "2.3.0",
        "engine": "yt-dlp",
        "facebook_share_resolver": True,
        "tiktok_shortlink_resolver": True,
        "reddit_share_resolver": True,
        "tiktok_impersonation": "extractor-auto",
        "cookies_loaded": bool(COOKIE_FILE),
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": "2.3.0"}


@app.post("/extract")
def extract_rich(req: ExtractRequest):
    info = extract(req)
    entries = info.get("entries")
    if entries:
        items = []
        for x in entries:
            if not x:
                continue
            u, headers = pick_direct(x)
            if u:
                items.append({"url": u, "filename": safe_filename(x), "headers": headers})
        if not items:
            raise HTTPException(status_code=422, detail={"code": "extract.empty", "message": "No downloadable items found."})
        return {"status": "picker", "resolved_url": info.get("_ums_resolved_url"), "items": items}

    direct, headers = pick_direct(info)
    if not direct:
        raise HTTPException(status_code=422, detail={"code": "extract.empty", "message": "No direct media URL was found."})
    return {
        "status": "ok",
        "service": info.get("extractor_key") or info.get("extractor"),
        "filename": safe_filename(info),
        "resolved_url": info.get("_ums_resolved_url"),
        "media": {"url": direct, "http_headers": headers, "thumbnail": info.get("thumbnail")},
    }


@app.post("/")
def cobalt_compatible(req: ExtractRequest):
    try:
        info = extract(req)
        entries = info.get("entries")
        if entries:
            picker = []
            for x in entries:
                if not x:
                    continue
                direct, headers = pick_direct(x)
                if direct:
                    picker.append({
                        "type": "video" if x.get("vcodec") not in (None, "none") else "photo",
                        "url": direct,
                        "thumb": x.get("thumbnail"),
                        "filename": safe_filename(x),
                        "headers": headers,
                    })
            if not picker:
                raise HTTPException(status_code=422, detail={"code": "extract.empty", "message": "No downloadable items found."})
            return {"status": "picker", "picker": picker, "resolvedUrl": info.get("_ums_resolved_url")}

        direct, headers = pick_direct(info)
        if not direct:
            raise HTTPException(status_code=422, detail={"code": "extract.empty", "message": "No direct media URL was found.", "resolved_url": info.get("_ums_resolved_url")})
        return {
            "status": "redirect",
            "url": direct,
            "filename": safe_filename(info),
            "headers": headers,
            "resolvedUrl": info.get("_ums_resolved_url"),
        }
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={
            "status": "error",
            "error": {
                "code": detail.get("code", "extract.failed"),
                "message": detail.get("message", "Extraction failed"),
                "hint": detail.get("hint"),
                "resolvedUrl": detail.get("resolved_url"),
                "engine": "yt-dlp",
            },
        })


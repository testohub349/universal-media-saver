import os
import re
import html
import base64
import tempfile
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import yt_dlp

app = FastAPI(title="Universal Media Saver API", version="2.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

COOKIE_FILE = None

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 15; SM-A065F) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DESKTOP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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


def quality_height(q: Optional[str]) -> Optional[int]:
    if not q or q == "max":
        return None
    try:
        return int(q)
    except Exception:
        return None


def normalize_input_url(raw: str) -> str:
    s = (raw or "").strip().replace("\u200b", "")
    md = re.search(r"\[[^\]]*\]\((https?://[^)\s]+)\)", s, re.I)
    if md:
        s = md.group(1)
    else:
        found = re.search(r"https?://[^\s)\]}>\"']+", s, re.I)
        if found:
            s = found.group(0)
    while s.endswith((".", ",", ";", ")")):
        s = s[:-1]
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s.lstrip("/")
    return s


def is_facebook_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return host == "fb.watch" or host.endswith(".facebook.com") or host == "facebook.com"


def is_tiktok_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"} or host.endswith(".tiktok.com")


def resolve_tiktok_short_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower().split(":")[0]
        if host not in {"vm.tiktok.com", "vt.tiktok.com"}:
            return url
        req = UrlRequest(url, headers=DESKTOP_HEADERS, method="GET")
        with urlopen(req, timeout=15) as resp:
            final_url = resp.geturl()
            if is_tiktok_host(urlparse(final_url).netloc):
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
        return any(x in path for x in ("/reel/", "/reels/", "/videos/", "/watch/", "/watch")) or \
               path.endswith("/watch") or "v=" in (p.query or "") or \
               path.endswith("/permalink.php") or path.endswith("/story.php")
    except Exception:
        return False


def clean_facebook_url(url: str) -> str:
    try:
        p = urlparse(url)
        if not is_facebook_host(p.netloc):
            return url
        if "/watch" in (p.path or "").lower() and "v=" in (p.query or ""):
            keep = []
            for part in p.query.split("&"):
                if part.startswith("v="):
                    keep.append(part)
            return urlunparse((p.scheme or "https", p.netloc, p.path, p.params, "&".join(keep), ""))
        if any(x in (p.path or "").lower() for x in ("/reel/", "/reels/", "/videos/")):
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
        if not is_facebook_host(host) or not needs_resolution:
            return clean_facebook_url(url)

        req = UrlRequest(url, headers=BROWSER_HEADERS, method="GET")
        with urlopen(req, timeout=15) as resp:
            final_url = resp.geturl()
            if looks_like_facebook_media_url(final_url):
                return clean_facebook_url(final_url)

            body = resp.read(512 * 1024).decode("utf-8", errors="ignore")
            patterns = [
                r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
                r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
            ]
            for pat in patterns:
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
        if is_facebook_host(host):
            return resolve_facebook_share_url(url)
        if is_tiktok_host(host):
            return resolve_tiktok_short_url(url)
    except Exception:
        pass
    return url


def ydl_opts(req: ExtractRequest, target_url: Optional[str] = None) -> Dict[str, Any]:
    h = quality_height(req.videoQuality)
    mode = (req.downloadMode or "auto").lower()

    if mode == "audio":
        fmt = "bestaudio/best"
    elif mode == "mute":
        fmt = f"bestvideo[height<={h}]" if h else "bestvideo"
    else:
        if h:
            fmt = f"best[height<={h}]/bestvideo[height<={h}]+bestaudio/best"
        else:
            fmt = "best/bestvideo+bestaudio"

    tiktok = False
    try:
        tiktok = is_tiktok_host(urlparse(target_url or req.url).netloc)
    except Exception:
        pass

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": fmt,
        "noplaylist": False,
        "extract_flat": False,
        "nocheckcertificate": True,
        "socket_timeout": 25,
        "retries": 3 if tiktok else 2,
        "fragment_retries": 3 if tiktok else 2,
        "http_headers": {
            "User-Agent": DESKTOP_HEADERS["User-Agent"] if tiktok else BROWSER_HEADERS["User-Agent"],
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.tiktok.com/" if tiktok else "",
        },
    }
    if tiktok:
        opts["impersonate"] = True
    if COOKIE_FILE:
        opts["cookiefile"] = COOKIE_FILE
    return opts


def normalize_entry(info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": info.get("id"),
        "title": info.get("title") or info.get("fulltitle"),
        "description": info.get("description"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "webpage_url": info.get("webpage_url"),
        "url": info.get("url"),
        "ext": info.get("ext"),
        "width": info.get("width"),
        "height": info.get("height"),
        "filesize": info.get("filesize") or info.get("filesize_approx"),
        "http_headers": info.get("http_headers") or {},
    }


def safe_filename(info: Dict[str, Any]) -> str:
    title = (info.get("title") or "media").strip()
    bad = '<>:"/\\|?*'
    for ch in bad:
        title = title.replace(ch, "_")
    title = " ".join(title.split())[:120]
    ext = info.get("ext") or "mp4"
    return f"{title}.{ext}"


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
        is_tt = False
        try:
            is_tt = is_tiktok_host(urlparse(target_url).netloc)
        except Exception:
            pass
        hint = (
            "TikTok short links are resolved automatically and browser impersonation is enabled. A removed/private/region-restricted TikTok can still fail."
            if is_tt else
            "Facebook share links are resolved automatically. Private/login-only media may still require cookies."
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "extract.failed",
                "message": str(e),
                "hint": hint,
                "resolved_url": target_url,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "extract.internal", "message": str(e), "resolved_url": target_url},
        )


@app.get("/")
def root():
    return {
        "status": "ok",
        "name": "Universal Media Saver API",
        "version": "2.2.0",
        "engine": "yt-dlp",
        "facebook_share_resolver": True,
        "tiktok_shortlink_resolver": True,
        "tiktok_impersonation": True,
        "cookies_loaded": bool(COOKIE_FILE),
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": "2.2.0"}


@app.post("/extract")
def extract_rich(req: ExtractRequest):
    info = extract(req)

    entries = info.get("entries")
    if entries:
        clean = [normalize_entry(x) for x in entries if x]
        return {
            "status": "picker",
            "service": info.get("extractor_key") or info.get("extractor"),
            "title": info.get("title"),
            "resolved_url": info.get("_ums_resolved_url"),
            "items": clean,
        }

    item = normalize_entry(info)
    if not item.get("url"):
        raise HTTPException(
            status_code=422,
            detail={"code": "extract.empty", "message": "No direct media URL was found."},
        )

    return {
        "status": "ok",
        "service": info.get("extractor_key") or info.get("extractor"),
        "filename": safe_filename(info),
        "resolved_url": info.get("_ums_resolved_url"),
        "media": item,
    }


@app.post("/")
def cobalt_compatible(req: ExtractRequest):
    try:
        info = extract(req)

        entries = info.get("entries")
        if entries:
            picker = []
            for x in entries:
                if not x or not x.get("url"):
                    continue
                picker.append({
                    "type": "video" if (x.get("vcodec") not in (None, "none")) else "photo",
                    "url": x.get("url"),
                    "thumb": x.get("thumbnail"),
                    "filename": safe_filename(x),
                })
            if not picker:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "extract.empty", "message": "No downloadable items found."},
                )
            return {
                "status": "picker",
                "picker": picker,
                "resolvedUrl": info.get("_ums_resolved_url"),
            }

        direct = info.get("url")
        if not direct:
            raise HTTPException(
                status_code=422,
                detail={"code": "extract.empty", "message": "No direct media URL was found."},
            )

        return {
            "status": "redirect",
            "url": direct,
            "filename": safe_filename(info),
            "headers": info.get("http_headers") or {},
            "resolvedUrl": info.get("_ums_resolved_url"),
        }
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": "error",
                "error": {
                    "code": detail.get("code", "extract.failed"),
                    "message": detail.get("message", "Extraction failed"),
                    "hint": detail.get("hint"),
                    "resolvedUrl": detail.get("resolved_url"),
                    "engine": "yt-dlp",
                },
            },
        )

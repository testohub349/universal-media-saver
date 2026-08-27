import os
import base64
import tempfile
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import yt_dlp

app = FastAPI(title="Universal Media Saver API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

COOKIE_FILE = None

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

def ydl_opts(req: ExtractRequest) -> Dict[str, Any]:
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

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": fmt,
        "noplaylist": False,
        "extract_flat": False,
        "nocheckcertificate": True,
        "socket_timeout": 25,
        "retries": 2,
        "fragment_retries": 2,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
    }
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
    try:
        with yt_dlp.YoutubeDL(ydl_opts(req)) as ydl:
            info = ydl.extract_info(req.url, download=False)
            if not info:
                raise RuntimeError("Extractor returned no media data")
            return info
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "extract.failed",
                "message": str(e),
                "hint": "Some Facebook/Instagram links may require cookies or may block datacenter IPs."
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "extract.internal", "message": str(e)},
        )

@app.get("/")
def root():
    return {
        "status": "ok",
        "name": "Universal Media Saver API",
        "version": "2.0.0",
        "engine": "yt-dlp",
        "cookies_loaded": bool(COOKIE_FILE),
    }

@app.get("/health")
def health():
    return {"status": "ok"}

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
        "media": item,
    }

@app.post("/")
def cobalt_compatible(req: ExtractRequest):
    """
    Cobalt-like compatibility endpoint for the existing Netlify frontend.
    Returns redirect/picker/error-style payloads.
    """
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
        return {"status": "picker", "picker": picker}

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
    }

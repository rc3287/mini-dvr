#!/usr/bin/env python3
"""
Mini-DVR RTSP - Backend Server
FastAPI server managing buffer, HLS playlist and network scanner
"""

import asyncio
import glob
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ─── Configuration ──────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent
BUFFER_DIR  = BASE_DIR / "buffer"
FRONTEND_DIR= BASE_DIR / "frontend"
CONFIG_FILE = BASE_DIR / "config.json"

BUFFER_DIR.mkdir(exist_ok=True)

DEFAULT_CONFIG = {
    "rtsp_url":       "",
    "buffer_minutes": 30,
    "segment_seconds": 2,
    "scan_network":   "192.168.1.0/24",
    "rtsp_port":      554,
    "hls_port":       8080,
}

# ─── Load / save config ──────────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # Fill in missing keys from defaults
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="Mini-DVR API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── State ───────────────────────────────────────────────────────────────────
ffmpeg_process: Optional[asyncio.subprocess.Process] = None
recorder_task:  Optional[asyncio.Task] = None


# ════════════════════════════════════════════════════════════════════════════
# Helper: get sorted segment list with timestamps
# ════════════════════════════════════════════════════════════════════════════
def get_segments() -> list[dict]:
    """Return sorted list of segments with metadata."""
    files = sorted(glob.glob(str(BUFFER_DIR / "segment_*.ts")))
    segments = []
    for f in files:
        p = Path(f)
        stat = p.stat()
        name = p.name
        # Extract index from filename
        m = re.search(r"segment_(\d+)\.ts", name)
        idx = int(m.group(1)) if m else 0
        segments.append({
            "file":  str(p),
            "name":  name,
            "index": idx,
            "mtime": stat.st_mtime,
            "size":  stat.st_size,
        })
    # Sort by mtime to handle wrap-around correctly
    segments.sort(key=lambda x: x["mtime"])
    return segments


def segments_in_window(buffer_minutes: int) -> list[dict]:
    """Return only segments within the configured buffer window."""
    all_segs  = get_segments()
    if not all_segs:
        return []
    cutoff = time.time() - buffer_minutes * 60
    return [s for s in all_segs if s["mtime"] >= cutoff]


# ════════════════════════════════════════════════════════════════════════════
# HLS playlist generation
# ════════════════════════════════════════════════════════════════════════════
def build_playlist(segs: list[dict], seg_duration: int) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{seg_duration + 1}",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    for s in segs:
        lines.append(f"#EXTINF:{seg_duration}.0,")
        lines.append(f"/segments/{s['name']}")
    return "\n".join(lines) + "\n"


def build_live_playlist(seg_duration: int) -> str:
    """Rolling live playlist (last 5 segments)."""
    all_segs = get_segments()
    live_segs = all_segs[-5:] if len(all_segs) >= 5 else all_segs
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{seg_duration + 1}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-ALLOW-CACHE:NO",
    ]
    for s in live_segs:
        lines.append(f"#EXTINF:{seg_duration}.0,")
        lines.append(f"/segments/{s['name']}")
    return "\n".join(lines) + "\n"


# ════════════════════════════════════════════════════════════════════════════
# FFmpeg recorder
# ════════════════════════════════════════════════════════════════════════════
async def run_recorder():
    global ffmpeg_process
    cfg = load_config()
    url = cfg["rtsp_url"]
    seg = cfg["segment_seconds"]
    buf = cfg["buffer_minutes"]
    max_segs = (buf * 60) // seg

    cmd = [
        "ffmpeg",
        "-loglevel", "warning",
        "-rtsp_transport", "tcp",
        "-i", url,
        "-c", "copy",
        "-f", "hls",
        "-hls_time", str(seg),
        "-hls_list_size", "6",
        "-hls_flags", "omit_endlist",
        "-hls_base_url", "/segments/",
        "-hls_segment_filename", str(BUFFER_DIR / "segment_%04d.ts"),
        str(BUFFER_DIR / "live.m3u8"),
    ]

    while True:
        (BUFFER_DIR / "live.m3u8").unlink(missing_ok=True)
        print(f"[recorder] Starting FFmpeg: {' '.join(cmd)}")
        try:
            ffmpeg_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await ffmpeg_process.communicate()
            code = ffmpeg_process.returncode
            print(f"[recorder] FFmpeg exited ({code}): {stderr.decode()[-200:] if stderr else ''}")
        except Exception as e:
            print(f"[recorder] Error: {e}")

        print("[recorder] Restarting in 5 s …")
        await asyncio.sleep(5)


# ════════════════════════════════════════════════════════════════════════════
# Network scanner (runs in thread pool to avoid blocking)
# ════════════════════════════════════════════════════════════════════════════
def _scan_network(network: str, port: int = 554) -> list[dict]:
    """
    Multi-stage scan:
    1. nmap ping sweep
    2. port 554 open check
    3. basic RTSP OPTIONS probe
    """
    results = []

    # Stage 1 — host discovery
    try:
        r = subprocess.run(
            ["nmap", "-sn", "-T4", network, "--open", "-oG", "-"],
            capture_output=True, text=True, timeout=30
        )
        hosts = re.findall(r"Host: ([\d.]+)", r.stdout)
    except FileNotFoundError:
        # nmap not available, fall back to arp-scan or ping sweep
        hosts = []
        try:
            r = subprocess.run(
                ["arp-scan", "--localnet"],
                capture_output=True, text=True, timeout=30
            )
            hosts = re.findall(r"([\d]+\.[\d]+\.[\d]+\.[\d]+)", r.stdout)
        except Exception:
            pass

    # Stage 2+3 — port + RTSP probe per host
    for host in hosts:
        try:
            r = subprocess.run(
                ["nc", "-z", "-w", "1", host, str(port)],
                capture_output=True, timeout=2
            )
            if r.returncode != 0:
                continue
        except Exception:
            continue

        # RTSP OPTIONS probe
        rtsp_url = f"rtsp://{host}:{port}/"
        entry = {"ip": host, "port": port, "rtsp_url": rtsp_url, "status": "open"}
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-rtsp_transport", "tcp",
                 "-i", rtsp_url, "-show_entries", "stream=codec_name,width,height",
                 "-of", "json"],
                capture_output=True, text=True, timeout=5
            )
            if probe.returncode == 0:
                entry["status"] = "rtsp_ok"
                try:
                    info = json.loads(probe.stdout)
                    entry["streams"] = info.get("streams", [])
                except Exception:
                    pass
        except Exception:
            pass

        results.append(entry)

    return results


# ════════════════════════════════════════════════════════════════════════════
# API routes
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
async def set_config(body: dict):
    cfg = load_config()
    cfg.update(body)
    # Clamp buffer
    cfg["buffer_minutes"] = max(10, min(60, int(cfg.get("buffer_minutes", 30))))
    cfg["segment_seconds"] = max(1, min(10, int(cfg.get("segment_seconds", 2))))
    save_config(cfg)
    # Restart recorder if RTSP URL changed
    if "rtsp_url" in body:
        await restart_recorder()
    return {"ok": True, "config": cfg}


@app.get("/api/status")
def get_status():
    cfg   = load_config()
    segs  = segments_in_window(cfg["buffer_minutes"])
    total = len(segs)
    duration = total * cfg["segment_seconds"]

    oldest = segs[0]["mtime"]  if segs else None
    newest = segs[-1]["mtime"] if segs else None

    return {
        "recording":       ffmpeg_process is not None and ffmpeg_process.returncode is None,
        "rtsp_url":        cfg["rtsp_url"],
        "segment_count":   total,
        "buffer_minutes":  cfg["buffer_minutes"],
        "segment_seconds": cfg["segment_seconds"],
        "buffer_duration_seconds": duration,
        "oldest_segment_time": oldest,
        "newest_segment_time": newest,
    }


@app.get("/api/segments")
def list_segments():
    cfg  = load_config()
    segs = segments_in_window(cfg["buffer_minutes"])
    return {"segments": segs, "count": len(segs)}


@app.get("/api/playlist.m3u8")
def full_playlist():
    """Full buffer playlist for timeline scrubbing."""
    cfg  = load_config()
    segs = segments_in_window(cfg["buffer_minutes"])
    m3u8 = build_playlist(segs, cfg["segment_seconds"])
    return PlainTextResponse(m3u8, media_type="application/vnd.apple.mpegurl")


@app.get("/api/live.m3u8")
def live_playlist():
    """Serve FFmpeg-generated HLS live playlist."""
    path = BUFFER_DIR / "live.m3u8"
    if not path.exists():
        raise HTTPException(404, "No live playlist yet")
    return FileResponse(str(path), media_type="application/vnd.apple.mpegurl")


@app.get("/api/clip.m3u8")
def clip_playlist(
    from_time: float = Query(..., description="Unix timestamp start"),
    to_time:   float = Query(..., description="Unix timestamp end"),
):
    """Generate a playlist for a specific time range."""
    cfg  = load_config()
    segs = segments_in_window(cfg["buffer_minutes"])
    clip = [s for s in segs if from_time <= s["mtime"] <= to_time]
    if not clip:
        raise HTTPException(404, "No segments in requested range")
    m3u8 = build_playlist(clip, cfg["segment_seconds"])
    m3u8 += "#EXT-X-ENDLIST\n"
    return PlainTextResponse(m3u8, media_type="application/vnd.apple.mpegurl")


@app.get("/segments/{filename}")
def serve_segment(filename: str):
    """Serve a .ts segment file."""
    if not re.match(r"^segment_\d+\.ts$", filename):
        raise HTTPException(400, "Invalid filename")
    path = BUFFER_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Segment not found")
    return FileResponse(str(path), media_type="video/mp2t")


@app.post("/api/recorder/start")
async def start_recorder():
    global recorder_task
    cfg = load_config()
    if not cfg.get("rtsp_url"):
        raise HTTPException(400, "No RTSP URL configured")
    if recorder_task and not recorder_task.done():
        return {"ok": True, "message": "Already running"}
    recorder_task = asyncio.create_task(run_recorder())
    return {"ok": True, "message": "Recorder started"}


@app.post("/api/recorder/stop")
async def stop_recorder():
    global ffmpeg_process, recorder_task
    if recorder_task:
        recorder_task.cancel()
        recorder_task = None
    if ffmpeg_process:
        try:
            ffmpeg_process.terminate()
            await asyncio.wait_for(ffmpeg_process.wait(), timeout=5)
        except Exception:
            ffmpeg_process.kill()
        ffmpeg_process = None
    return {"ok": True, "message": "Recorder stopped"}


async def restart_recorder():
    await stop_recorder()
    await asyncio.sleep(1)
    cfg = load_config()
    if cfg.get("rtsp_url"):
        global recorder_task
        recorder_task = asyncio.create_task(run_recorder())


@app.get("/api/scan")
async def scan_network():
    """Scan local network for RTSP cameras."""
    cfg = load_config()
    network = cfg.get("scan_network", "192.168.1.0/24")
    port    = cfg.get("rtsp_port", 554)
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, _scan_network, network, port)
    return {"cameras": results, "network": network}


@app.post("/api/clear-buffer")
async def clear_buffer():
    """Delete all segments from buffer."""
    for f in BUFFER_DIR.glob("segment_*.ts"):
        f.unlink(missing_ok=True)
    return {"ok": True}


async def cleanup_buffer_loop():
    """Delete segments older than buffer_minutes every 60 s."""
    while True:
        await asyncio.sleep(60)
        cfg = load_config()
        cutoff = time.time() - cfg["buffer_minutes"] * 60
        for f in BUFFER_DIR.glob("segment_*.ts"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except Exception:
                pass


@app.on_event("startup")
async def startup():
    asyncio.create_task(cleanup_buffer_loop())
    cfg = load_config()
    if cfg.get("rtsp_url"):
        global recorder_task
        recorder_task = asyncio.create_task(run_recorder())
        print(f"[startup] Auto-starting recorder for {cfg['rtsp_url']}")


# ─── Serve frontend SPA ──────────────────────────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

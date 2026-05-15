# Mini-DVR RTSP

A lightweight DVR for Linux — RTSP → ring buffer → web player with timeline.  
No cloud. No transcodage. Runs on an old PC.

---

## Features

| Feature | Details |
|---|---|
| Live view | HLS via browser, near-zero lag |
| Ring buffer | 10 – 60 minutes, configurable |
| Timeline | Click anywhere to jump back |
| DVR mode | Play any point in buffer |
| Return to live | One click |
| Camera discovery | Auto-scan LAN for RTSP cameras |
| Copy-stream | FFmpeg copy mode — minimal CPU |
| Auto-restart | Reconnects after RTSP drops |
| Systemd ready | Auto-start on boot |

---

## Quick start

```bash
# 1. Clone / copy the project
git clone <repo> mini-dvr
cd mini-dvr

# 2. Install (first time only)
chmod +x scripts/install.sh scripts/run.sh
./scripts/install.sh

# 3. Run
./scripts/run.sh

# 4. Open browser
# http://localhost:8080
```

---

## First use

1. Open **http://localhost:8080**
2. Click **⚙ Settings** in the header
3. Enter your RTSP URL: `rtsp://admin:password@192.168.1.50/stream`
4. Click **Save & restart**

**Or use Camera Discovery:**
1. Enter your network CIDR (e.g. `192.168.1.0/24`)
2. Click **⊕ Scan network**
3. Click any result to auto-fill the URL
4. Save & restart

---

## Configuration reference

`config.json` at the project root (also editable via the web UI):

```json
{
  "rtsp_url":        "rtsp://user:pass@192.168.1.50/stream",
  "buffer_minutes":  30,
  "segment_seconds": 2,
  "scan_network":    "192.168.1.0/24",
  "rtsp_port":       554,
  "hls_port":        8080
}
```

| Key | Default | Description |
|---|---|---|
| `rtsp_url` | `""` | Full RTSP stream URL |
| `buffer_minutes` | `30` | Ring buffer size (10–60) |
| `segment_seconds` | `2` | Segment duration (1–10) |
| `scan_network` | `192.168.1.0/24` | CIDR to scan |
| `rtsp_port` | `554` | Port to probe during scan |

---

## Architecture

```
RTSP camera
    │
    │  TCP (copy-stream, no transcoding)
    ▼
FFmpeg segmenter
    │
    │  .ts segments  (segment_XXXX.ts)
    ▼
buffer/          ← ring buffer on disk
    │
    │  HLS playlist
    ▼
FastAPI backend  (port 8080)
    │
    │  /api/live.m3u8   — rolling 5-segment live playlist
    │  /api/playlist.m3u8 — full buffer playlist
    │  /api/clip.m3u8   — time-range clip playlist
    │  /api/segments    — segment list + timestamps
    │  /api/scan        — LAN camera discovery
    │  /api/config      — read/write config
    │  /segments/*.ts   — serve individual segments
    ▼
Browser (HLS.js)
    │
    │  Live view / timeline / DVR scrub
    ▼
User
```

---

## Timeline & DVR usage

- The **timeline bar** covers your full buffer window
- **Hover** to preview timestamps
- **Click** any point to jump to that moment (DVR mode)
- The **● DVR** badge appears when you're in playback mode
- **▶ LIVE** button returns to the live edge instantly

---

## Buffer sizing

| Buffer | Segments (2 s each) | Approx. disk usage* |
|---|---|---|
| 10 min | 300 | ~180 MB |
| 30 min | 900 | ~540 MB |
| 60 min | 1800 | ~1.1 GB |

*Varies greatly by camera resolution and bitrate. A 1080p H.264 camera at 2 Mbps uses ~900 MB/hour.

---

## Dependencies

- **Python 3.10+**
- **FFmpeg** (system package)
- **nmap** (for camera discovery)
- **netcat** (nc, for port probing)
- Python packages: `fastapi`, `uvicorn`

---

## Systemd service

```bash
# After install.sh, or manually:
sudo cp systemd/mini-dvr.service /etc/systemd/system/
# Edit User= and WorkingDirectory= in the file
sudo systemctl daemon-reload
sudo systemctl enable mini-dvr
sudo systemctl start mini-dvr

# View logs:
journalctl -u mini-dvr -f
```

---

## Troubleshooting

**No video / blank player**
- Check RTSP URL is correct and reachable: `ffprobe rtsp://...`
- Check backend is running: `curl http://localhost:8080/api/status`
- Check buffer has segments: `ls buffer/`

**High CPU**
- Verify FFmpeg is using copy mode (default): `ps aux | grep ffmpeg`
- Avoid re-encoding — do NOT set `-c:v libx264`

**Camera not found by scan**
- Check CIDR matches your network: `ip addr`
- Install nmap: `sudo apt install nmap`
- Try specifying the RTSP URL manually

**RTSP keeps disconnecting**
- Backend auto-restarts FFmpeg after 5 s
- Check camera firmware / network stability

---

## License

MIT — use freely, self-host, modify.

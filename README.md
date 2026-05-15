# Mini-DVR RTSP

A lightweight DVR for Linux — RTSP → HLS ring buffer → web player with timeline scrubbing.  
No cloud. No transcoding. Runs on an old PC or a Raspberry Pi.

---

## Features

| Feature | Details |
|---|---|
| Live view | HLS via browser, ~7 s latency |
| Ring buffer | 10–60 minutes, configurable |
| Timeline | Click anywhere to jump back in time |
| DVR playback | Play any moment in the buffer |
| Return to live | One click |
| Camera discovery | Auto-scan LAN for RTSP cameras |
| Copy-stream | FFmpeg copy mode — no transcoding, minimal CPU |
| Auto-restart | Reconnects automatically after RTSP drops |
| Buffer cleanup | Segments older than `buffer_minutes` are deleted automatically |
| Systemd ready | Auto-start on boot |

---

## Quick start

```bash
git clone https://github.com/rc3287/mini-dvr
cd mini-dvr

chmod +x scripts/install.sh scripts/run.sh
./scripts/install.sh   # creates .venv, installs Python deps
./scripts/run.sh       # starts FastAPI on port 8080
```

Open **http://localhost:8080**, click **⚙ Settings**, enter your RTSP URL and save.

**Or use Camera Discovery:**
1. Enter your network CIDR (e.g. `192.168.1.0/24`)
2. Click **⊕ Scan network**
3. Click any result to auto-fill the URL
4. Click **Save & restart**

---

## Configuration

`config.json` at the project root (also editable live via the web UI):

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

| Key | Default | Notes |
|---|---|---|
| `rtsp_url` | `""` | Full RTSP URL including credentials |
| `buffer_minutes` | `30` | Clamped to 10–60. Controls disk usage and DVR window. |
| `segment_seconds` | `2` | Clamped to 1–10. Lower = less latency, more files. |
| `scan_network` | `192.168.1.0/24` | CIDR passed to nmap |
| `rtsp_port` | `554` | Port probed during camera scan |

> `config.json` is git-ignored — your credentials are never committed.

---

## Architecture

```
RTSP camera
    │  TCP transport (no transcoding)
    ▼
FFmpeg  (-f hls, copy mode)
    │
    ├── buffer/live.m3u8          rolling 6-segment live playlist
    └── buffer/segment_XXXX.ts   individual transport stream segments
    │
    ▼
FastAPI backend  (port 8080)
    │
    ├── GET  /api/live.m3u8       serve live.m3u8 directly (FileResponse)
    ├── GET  /api/playlist.m3u8   full buffer playlist for timeline
    ├── GET  /api/clip.m3u8       time-range clip playlist (DVR seek)
    ├── GET  /segments/*.ts       serve individual segments
    ├── GET  /api/status          recording state, buffer stats
    ├── GET  /api/segments        segment list with timestamps
    ├── GET  /api/scan            LAN camera discovery
    ├── GET  /api/config          read config
    ├── POST /api/config          write config + restart recorder
    ├── POST /api/recorder/start  start FFmpeg
    ├── POST /api/recorder/stop   stop FFmpeg
    └── POST /api/clear-buffer    delete all segments
    │
    ▼
Browser  (HLS.js)
    live view · timeline bar · DVR scrub · camera scan UI
```

### How the live playlist works

FFmpeg writes `buffer/live.m3u8` directly using its native HLS muxer (`-f hls`).  
The playlist is a rolling window of the last **6 segments** (`hls_list_size=6`).  
Old segments are **not** deleted by FFmpeg — a background task (`cleanup_buffer_loop`) removes files older than `buffer_minutes` every 60 seconds, freeing disk space while keeping the DVR window intact.

### Why `omit_endlist` (no `#EXT-X-ENDLIST`)

Omitting the end marker tells HLS.js the stream is live and to keep polling the playlist for new segments.

---

## Latency

With default settings (2 s segments):

| Stage | Time |
|---|---|
| FFmpeg segment encoding | ~2 s (one segment) |
| HLS.js live sync point (`liveSyncDurationCount=2`) | ~4 s behind live edge |
| Network + player buffer | ~1 s |
| **Total** | **~7 s** |

To reduce latency further, set `segment_seconds: 1` in config — this halves the minimum latency (~4 s) at the cost of twice as many files. True sub-second latency would require Low-Latency HLS (LL-HLS), which needs re-encoding.

---

## Buffer sizing

| Buffer | Segments @ 2 s | Approx. disk usage* |
|---|---|---|
| 10 min | 300 | ~180 MB |
| 30 min | 900 | ~540 MB |
| 60 min | 1800 | ~1.1 GB |

*Depends on camera resolution and bitrate. A 1080p H.264 stream at 2 Mbps uses ~900 MB/hour.

---

## API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | Recording state, segment count, buffer duration |
| `GET` | `/api/config` | Current configuration |
| `POST` | `/api/config` | Update config (body: JSON subset of config keys) |
| `GET` | `/api/live.m3u8` | Live HLS playlist (serve to HLS.js) |
| `GET` | `/api/playlist.m3u8` | Full buffer playlist |
| `GET` | `/api/clip.m3u8?from_time=&to_time=` | Clip playlist for a Unix timestamp range |
| `GET` | `/api/segments` | List of segments with mtime and size |
| `GET` | `/segments/{filename}` | Serve a `.ts` segment |
| `POST` | `/api/recorder/start` | Start FFmpeg |
| `POST` | `/api/recorder/stop` | Stop FFmpeg |
| `POST` | `/api/clear-buffer` | Delete all `.ts` segments from disk |
| `GET` | `/api/scan` | Scan LAN for RTSP cameras |

---

## Systemd service

```bash
sudo cp systemd/mini-dvr.service /etc/systemd/system/
# Edit User= and WorkingDirectory= to match your setup
sudo systemctl daemon-reload
sudo systemctl enable --now mini-dvr

# Follow logs
journalctl -u mini-dvr -f
```

---

## Dependencies

- **Python 3.10+**
- **FFmpeg** — `sudo apt install ffmpeg`
- **nmap** — `sudo apt install nmap` (camera discovery)
- **netcat** — `sudo apt install netcat` (port probing)
- Python: `fastapi`, `uvicorn[standard]`

---

## Troubleshooting

**Blank player / no video**
```bash
ffprobe rtsp://user:pass@192.168.1.50/stream   # test RTSP directly
curl http://localhost:8080/api/status           # check backend
ls buffer/                                      # check segments exist
```

**High CPU usage**
- FFmpeg must run in copy mode (default). Verify: `ps aux | grep ffmpeg` should show `-c copy`, not `-c:v libx264`.

**13+ second latency**
- Delete `buffer/live.m3u8` and restart the recorder — the playlist may have accumulated thousands of entries from a previous `append_list` session.

**Camera not found by scan**
- Verify your CIDR: `ip addr`
- `sudo apt install nmap`
- Enter the RTSP URL manually in Settings

**RTSP keeps dropping**
- The recorder auto-restarts after 5 s. Check camera firmware and network stability.
- Some cameras require `-rtsp_transport udp` — edit the FFmpeg command in `backend/server.py`.

---

## License

MIT — use freely, self-host, modify.

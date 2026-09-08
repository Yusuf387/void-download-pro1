import os, re, uuid, time, shutil, threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, abort
import yt_dlp

BASE = Path(__file__).resolve().parent
DOWNLOADS = BASE / "downloads"
DOWNLOADS.mkdir(exist_ok=True)
PORT = int(os.getenv("PORT", "8080"))
MAX_AGE_HOURS = int(os.getenv("MAX_AGE_HOURS", "2"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "3"))
RATE_WINDOW = int(os.getenv("RATE_WINDOW_SECONDS", "60"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "10"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # API JSON, not media

URL_RE = re.compile(r"^https?://", re.I)
jobs = {}
jobs_lock = threading.Lock()
rate = {}
rate_lock = threading.Lock()
slots = threading.BoundedSemaphore(MAX_CONCURRENT)

def cleanup():
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    for p in DOWNLOADS.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        except OSError:
            pass

def client_ip():
    # In production, only trust X-Forwarded-For if your reverse proxy overwrites it.
    return request.remote_addr or "unknown"

def limited():
    now = time.time()
    ip = client_ip()
    with rate_lock:
        hits = [t for t in rate.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_LIMIT:
            rate[ip] = hits
            return True
        hits.append(now)
        rate[ip] = hits
    return False

def safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|]+', "_", name or "video")
    return name[:100].strip() or "video"

def update(job, **kwargs):
    with jobs_lock:
        jobs.setdefault(job, {}).update(kwargs)

def run_download(job, url, mode, quality):
    jobdir = DOWNLOADS / job
    jobdir.mkdir(parents=True, exist_ok=True)
    update(job, status="downloading", progress=0, message="Downloading…")
    try:
        if mode == "audio":
            fmt = "bestaudio/best"
            pp = [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]
            merge = None
        else:
            if quality == "best":
                fmt = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
            elif quality in {"1080","720","480","360"}:
                q = int(quality)
                fmt = f"bv*[height<={q}]+ba[ext=m4a]/b[height<={q}]/b"
            else:
                fmt = "b[ext=mp4]/b"
            pp = []
            merge = "mp4"

        def hook(d):
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                done = d.get("downloaded_bytes", 0)
                pct = int(done * 100 / total) if total else 0
                update(job, progress=max(0, min(99, pct)), message="Downloading…")
            elif status == "finished":
                update(job, progress=99, message="Finalizing…")

        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "outtmpl": str(jobdir / "%(title).100s.%(ext)s"),
            "format": fmt, "merge_output_format": merge,
            "postprocessors": pp, "restrictfilenames": True,
            "progress_hooks": [hook],
        }
        with slots:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

        candidates = [p for p in jobdir.iterdir() if p.is_file()]
        if not candidates:
            raise RuntimeError("No file was produced.")
        final = max(candidates, key=lambda p: p.stat().st_mtime)
        name = safe_filename(info.get("title")) + final.suffix.lower()
        update(job, status="done", progress=100, message="Ready", filename=name, path=str(final))
    except Exception as exc:
        shutil.rmtree(jobdir, ignore_errors=True)
        update(job, status="error", progress=0, message="Download failed.")

@app.get("/")
def home():
    cleanup()
    return render_template("index.html")

@app.post("/api/info")
def api_info():
    cleanup()
    if limited():
        return jsonify(error="Too many requests. Please wait a minute and try again."), 429
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not URL_RE.match(url):
        return jsonify(error="Enter a valid public http(s) URL."), 400
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            meta = ydl.extract_info(url, download=False)
        return jsonify({
            "title": meta.get("title") or "Untitled video",
            "uploader": meta.get("uploader") or "",
            "duration": meta.get("duration") or 0,
            "thumbnail": meta.get("thumbnail") or "",
            "webpage_url": meta.get("webpage_url") or url
        })
    except Exception:
        return jsonify(error="This URL could not be processed. It may be private, unsupported, protected, or temporarily unavailable."), 422

@app.post("/api/download")
def api_download():
    cleanup()
    if limited():
        return jsonify(error="Too many requests. Please wait a minute and try again."), 429
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    mode = data.get("mode", "video")
    quality = str(data.get("quality", "best"))
    if not URL_RE.match(url):
        return jsonify(error="Enter a valid public http(s) URL."), 400
    if mode not in {"video", "audio"}:
        return jsonify(error="Invalid format."), 400
    if quality not in {"best","1080","720","480","360"}:
        quality = "best"

    job = uuid.uuid4().hex
    update(job, status="queued", progress=0, message="Queued…", filename=None, path=None)
    threading.Thread(target=run_download, args=(job, url, mode, quality), daemon=True).start()
    return jsonify(job=job)

@app.get("/api/status/<job>")
def api_status(job):
    with jobs_lock:
        item = jobs.get(job)
    if not item:
        return jsonify(error="Job not found."), 404
    return jsonify({k:v for k,v in item.items() if k != "path"})

@app.get("/download/<job>")
def download_file(job):
    cleanup()
    with jobs_lock:
        item = jobs.get(job)
    if not item or item.get("status") != "done":
        abort(404)
    path = Path(item["path"]).resolve()
    if DOWNLOADS.resolve() not in path.parents or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=item["filename"])

@app.get("/terms")
def terms():
    return render_template("terms.html")

@app.errorhandler(413)
def too_large(_):
    return jsonify(error="Request too large."), 413

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)

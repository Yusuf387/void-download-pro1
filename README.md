# VOID Download Pro

A mobile-first public-media downloader built with Flask + yt-dlp.

## Run
Python 3.11+ and FFmpeg are recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:8080

## Docker
```bash
docker build -t void-download .
docker run --rm -p 8080:8080 void-download
```

## Production
Use HTTPS, a reverse proxy, persistent/temporary storage as appropriate, and a job queue for heavy traffic. Configure Cloudflare or another WAF/rate limiter. Do not accept cookies, credentials, arbitrary headers, or shell commands from users.

## Support
yt-dlp maintains a large supported-site list, but explicitly notes that sites can change and break extractors. No website can truthfully promise permanent support for every social platform.

## Rights
Only download content you own or have permission to save. Platform terms and copyright law can restrict downloading or reuse.

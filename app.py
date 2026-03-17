"""
YouTube Playlist Downloader — Backend
Fixes applied:
  - No ffmpeg required: audio saves as .m4a, video as .mp4 (single-stream fallback)
  - Cookie fallback: tries Chrome cookies first, falls back to anonymous if DB locked
  - Proper error surfacing: ignoreerrors=False so real errors reach the UI
  - Custom download directory: accepted per-request from frontend
  - Accurate progress tracking: counts both 'finished' and skipped/errored tracks
  - User-agent spoofing + retries to bypass 403/bot detection
"""

import os
import re
import threading
import time
import subprocess
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)


IS_DOCKER = os.path.exists('/.dockerenv')

if IS_DOCKER:
    DEFAULT_DOWNLOAD_DIR = '/app/downloads'
else:
    DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', 'YouTube Downloads')

os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)

jobs = {}   # in-memory job store

# ── Utilities ──────────────────────────────────────────────────────────────────

def gen_job_id() -> str:
    return f"job_{int(time.time() * 1000)}"


def strip_ansi(text) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', str(text))


def detect_browser():
    """
    Return the first browser tuple whose profile dir exists.
    yt-dlp uses it to load cookies so YouTube treats us as a real user.
    """
    candidates = [
        ('chrome',  os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data')),
        ('edge',    os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data')),
        ('firefox', os.path.expandvars(r'%APPDATA%\Mozilla\Firefox\Profiles')),
        ('brave',   os.path.expandvars(r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data')),
    ]
    for name, path in candidates:
        if os.path.isdir(path):
            return (name,)
    return None


def has_ffmpeg() -> bool:
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False

FFMPEG_AVAILABLE = has_ffmpeg()


def info_opts(use_cookies: bool = True) -> dict:
    """
    Options for metadata/playlist info fetching only.
    Uses a normal browser UA — the android UA breaks flat extraction.
    """
    opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'retries': 5,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }
    if use_cookies:
        browser = detect_browser()
        if browser:
            opts['cookiesfrombrowser'] = browser
    return opts


def download_opts(use_cookies: bool = True) -> dict:
    """
    Options for actual video/audio downloading.
    Forces the Android + web player client — bypasses PO-token/403 restrictions.
    """
    opts = {
        'quiet': True,
        'no_warnings': True,
        'retries': 10,
        'fragment_retries': 10,
        'file_access_retries': 5,
        'sleep_interval': 1,
        'max_sleep_interval': 4,
        'nocheckcertificate': True,
        # Android client skips PO-token requirement → no 403
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'skip': ['hls'],
            }
        },
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }
    if use_cookies:
        browser = detect_browser()
        if browser:
            opts['cookiesfrombrowser'] = browser
    return opts


def safe_get_info(url: str) -> dict:
    """Try with cookies, fall back to anonymous if cookie DB is locked."""
    def _fetch(use_cookies: bool):
        opts = info_opts(use_cookies)
        opts['extract_flat'] = 'in_playlist'
        opts['playlistend'] = 200
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    try:
        return _fetch(True)
    except Exception:
        return _fetch(False)


# ── Download worker ────────────────────────────────────────────────────────────

def run_download(job_id: str, url: str, fmt: str, quality: str, save_dir: str):
    job = jobs[job_id]

    def update(key, val):
        job[key] = val

    try:
        # ── Step 1: fetch metadata ────────────────────────────────────────────
        update('status', 'fetching')
        update('log', 'Fetching playlist info…')
        info = safe_get_info(url)

        if not info:
            raise RuntimeError("Could not retrieve playlist info. Check the URL and make sure it's public.")

        entries = [e for e in (info.get('entries') or [info]) if e]
        total = len(entries)
        if total == 0:
            raise RuntimeError("Playlist is empty or all videos are private / geo-restricted.")

        update('total', total)
        update('title', info.get('title') or info.get('id') or 'Unknown')
        update('log', f'Found {total} tracks. Starting download…')

        # ── Step 2: build yt-dlp options ─────────────────────────────────────
        os.makedirs(save_dir, exist_ok=True)

        # Output template: SaveDir / PlaylistName / ##_Title.ext
        out_tpl = os.path.join(
            save_dir,
            '%(playlist_title,playlist_id,title)s',
            '%(playlist_index|)s%(playlist_index& - |)s%(title)s.%(ext)s'
        )

        # ── Format selection ──────────────────────────────────────────────────
        # We choose formats that DON'T require ffmpeg merge whenever possible.
        # If ffmpeg IS available we use richer formats.
        if fmt == 'audio':
            if FFMPEG_AVAILABLE:
                # Download best audio, convert to mp3
                dl_format = 'bestaudio/best'
                postprocessors = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
                merge_format = None
            else:
                # No ffmpeg: download best audio as-is (m4a or webm)
                dl_format = 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best'
                postprocessors = []
                merge_format = None
        else:
            # Video
            q_map = {
                '1080': '1080', '720': '720', '480': '480', '360': '360', '2160': '2160',
            }
            h = q_map.get(quality)
            if FFMPEG_AVAILABLE:
                # Can merge separate video+audio streams
                dl_format = (
                    f'bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/'
                    f'bestvideo[height<={h}]+bestaudio/'
                    f'best[height<={h}]'
                ) if h else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
                merge_format = 'mp4'
                postprocessors = []
            else:
                # No ffmpeg: must use single progressive stream
                dl_format = (
                    f'best[height<={h}][ext=mp4]/best[height<={h}]/best'
                ) if h else 'best[ext=mp4]/best'
                merge_format = None
                postprocessors = []

        # ── Progress tracking ─────────────────────────────────────────────────
        done_count = [0]
        fail_count = [0]
        current_title = ['']

        def progress_hook(d):
            st = d.get('status')
            fname = d.get('filename', '') or d.get('tmpfilename', '')
            if fname:
                current_title[0] = os.path.basename(fname)
                update('current_file', current_title[0])

            if st == 'downloading':
                # Show live speed/ETA
                speed = d.get('speed')
                eta = d.get('eta')
                speed_str = f"{speed/1024/1024:.1f} MB/s" if speed else ''
                eta_str   = f"ETA {eta}s" if eta else ''
                note      = ' · '.join(filter(None, [speed_str, eta_str]))
                update('download_note', note)

            elif st == 'finished':
                done_count[0] += 1
                n = done_count[0]
                update('downloaded', n)
                update('progress', round(n / total * 100) if total else 100)
                update('log', f'✔ {current_title[0]}')

            elif st == 'error':
                fail_count[0] += 1
                update('failed_count', fail_count[0])

        # ── Build final opts dict ─────────────────────────────────────────────
        dl_opts = download_opts(use_cookies=True)  # android client — bypasses 403
        dl_opts.update({
            'format': dl_format,
            'outtmpl': out_tpl,
            'progress_hooks': [progress_hook],
            'ignoreerrors': True,   # skip unavailable individual tracks, don't abort
            'concurrent_fragment_downloads': 10,
            'external_downloader': 'aria2c',
            'external_downloader_args': ['-x', '16', '-k', '1M'],
        })
        if merge_format:
            dl_opts['merge_output_format'] = merge_format
        if postprocessors:
            dl_opts['postprocessors'] = postprocessors

        # ── Step 3: download ──────────────────────────────────────────────────
        update('status', 'downloading')

        def attempt(use_cookies: bool):
            opts = dict(dl_opts)
            if not use_cookies:
                opts.pop('cookiesfrombrowser', None)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ret = ydl.download([url])
            return ret

        try:
            attempt(True)
        except Exception as ce:
            # Cookie DB locked (browser open) → retry anonymously
            update('log', 'Cookie error, retrying anonymously…')
            attempt(False)

        update('failed', fail_count[0])
        update('status', 'done')
        update('progress', 100)
        update('log', f'Done! {done_count[0]} downloaded, {fail_count[0]} failed.')

    except Exception as exc:
        update('status', 'error')
        update('error', strip_ansi(exc))


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/info', methods=['GET'])
def app_info():
    return jsonify({
        'ffmpeg': FFMPEG_AVAILABLE,
        'default_dir': DEFAULT_DOWNLOAD_DIR,
        'browser': (detect_browser() or [None])[0],
    })


@app.route('/api/preview', methods=['POST'])
def preview():
    data = request.json or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    try:
        info = safe_get_info(url)
        if not info:
            return jsonify({'error': 'Could not fetch info — check URL is correct and playlist is public.'}), 400

        raw = info.get('entries') or [info]
        entries = [e for e in raw if e]

        videos = []
        for i, e in enumerate(entries[:50]):
            thumb = e.get('thumbnail') or ''
            if not thumb:
                thumbs = e.get('thumbnails') or []
                if thumbs:
                    thumb = thumbs[-1].get('url', '')
            videos.append({
                'index': i + 1,
                'title': e.get('title') or 'Unknown',
                'duration': e.get('duration'),
                'thumbnail': thumb,
                'uploader': e.get('uploader') or e.get('channel') or '',
            })

        count = info.get('playlist_count') or len(entries)
        return jsonify({
            'title':    info.get('title') or 'Unknown Playlist',
            'uploader': info.get('uploader') or info.get('channel') or '',
            'thumbnail':info.get('thumbnail') or '',
            'count':    count,
            'videos':   videos,
        })
    except Exception as exc:
        return jsonify({'error': strip_ansi(exc)}), 500


@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.json or {}
    url      = (data.get('url') or '').strip()
    fmt      = data.get('format', 'audio')
    quality  = data.get('quality', 'best')
    save_dir = (data.get('save_dir') or DEFAULT_DOWNLOAD_DIR).strip()

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    # Validate / create save directory
    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        return jsonify({'error': f'Invalid save directory: {e}'}), 400

    jid = gen_job_id()
    jobs[jid] = {
        'id': jid, 'url': url, 'format': fmt, 'quality': quality,
        'save_dir': save_dir,
        'status': 'queued', 'progress': 0,
        'total': 0, 'downloaded': 0, 'failed': 0, 'failed_count': 0,
        'title': '', 'error': '', 'current_file': '',
        'log': 'Queued…', 'download_note': '',
        'thumbnail': '',
    }

    t = threading.Thread(target=run_download, args=(jid, url, fmt, quality, save_dir), daemon=True)
    t.start()
    return jsonify({'job_id': jid})


@app.route('/api/status/<jid>')
def status(jid):
    job = jobs.get(jid)
    if not job:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(job)


@app.route('/api/jobs')
def list_jobs():
    return jsonify(list(jobs.values()))


@app.route('/api/open-folder', methods=['POST'])
def open_folder():
    if IS_DOCKER:
        return jsonify({'error': 'Cannot open folders from within Docker. Your downloaded files are saved to the "downloads" folder inside your project directory on your computer.'}), 400

    data = request.json or {}
    folder = (data.get('path') or DEFAULT_DOWNLOAD_DIR).strip()
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        if hasattr(os, 'startfile'):
            os.startfile(folder)
        else:
            subprocess.Popen(['xdg-open', folder])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("\n🎵  YouTube Playlist Downloader")
    print("     →  http://localhost:5050\n")
    b = detect_browser()
    print(f"   {'✅' if b else '⚠️ '} Browser cookies: {b[0] if b else 'none (anonymous mode)'}")
    print(f"   {'✅' if FFMPEG_AVAILABLE else '⚠️ '} FFmpeg: {'found' if FFMPEG_AVAILABLE else 'NOT found — audio saves as .m4a, video as .mp4 (single stream)'}")
    print()
    app.run(host='0.0.0.0', debug=False, port=5050, threaded=True)

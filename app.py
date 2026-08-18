"""
Viral Clipper — upload a raw video, AI finds the most "viral" moments,
cuts them into clips with burned-in subtitles, and publishes to YouTube.

Pipeline per uploaded video:
  1. Save upload
  2. Transcribe with faster-whisper (local, free, word-level timestamps)
  3. Send transcript to Gemini -> ask for the N best short segments
     (start/end, a punchy title, and a reason)
  4. For each segment: cut with ffmpeg, burn subtitles from the transcript
  5. Show clips in the UI so the user can pick which ones to publish
  6. Publish selected clips to YouTube via the YouTube Data API (OAuth)

Run:
  pip install -r requirements.txt
  cp .env.example .env   # fill in GEMINI_API_KEY
  python app.py
See README.md for full setup (ffmpeg, Google Cloud OAuth client, etc).
"""

import os
import json
import uuid
import threading
import subprocess
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, session, url_for
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
CLIP_DIR = BASE_DIR / "clips"
JOBS_DIR = BASE_DIR / "jobs"
TOKENS_DIR = BASE_DIR / "tokens"
for d in (UPLOAD_DIR, CLIP_DIR, JOBS_DIR, TOKENS_DIR):
    d.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["PREFERRED_URL_SCHEME"] = "https"

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NUM_CLIPS = int(os.environ.get("NUM_CLIPS_PER_VIDEO", "4"))
MAX_CLIP_SECONDS = int(os.environ.get("MAX_CLIP_SECONDS", "60"))

# On Railway/Render you set the OAuth client as an env var (GOOGLE_CLIENT_SECRET_JSON,
# the full JSON text from Google Cloud) instead of committing client_secret.json to
# the repo. If present, write it out once so the google-auth-oauthlib flow below
# (which expects a file) can read it normally.
CLIENT_SECRET_PATH = BASE_DIR / "client_secret.json"
if not CLIENT_SECRET_PATH.exists() and os.environ.get("GOOGLE_CLIENT_SECRET_JSON"):
    CLIENT_SECRET_PATH.write_text(os.environ["GOOGLE_CLIENT_SECRET_JSON"])

# ---------------------------------------------------------------------------
# Job state (simple JSON-file based store — fine for single-server use)
# ---------------------------------------------------------------------------

def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def save_job(job: dict):
    with open(job_path(job["id"]), "w") as f:
        json.dump(job, f, indent=2)


def load_job(job_id: str) -> dict | None:
    p = job_path(job_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def update_job(job_id: str, **fields):
    job = load_job(job_id)
    if job is None:
        return
    job.update(fields)
    save_job(job)


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes — upload + pipeline
# ---------------------------------------------------------------------------

@app.route("/api/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["video"]
    job_id = uuid.uuid4().hex[:12]
    ext = Path(f.filename).suffix or ".mp4"
    src_path = UPLOAD_DIR / f"{job_id}{ext}"
    f.save(src_path)

    job = {
        "id": job_id,
        "status": "queued",
        "source_file": str(src_path),
        "clips": [],
        "log": [],
    }
    save_job(job)

    thread = threading.Thread(target=process_video, args=(job_id, str(src_path)), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = load_job(job_id)
    if job is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/clips/<path:filename>")
def serve_clip(filename):
    return send_from_directory(CLIP_DIR, filename)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def log(job_id, message):
    job = load_job(job_id)
    job["log"].append(message)
    save_job(job)
    print(f"[{job_id}] {message}")


def process_video(job_id, src_path):
    try:
        update_job(job_id, status="transcribing")
        log(job_id, "Transcribing audio (faster-whisper)...")
        segments = transcribe(src_path)

        update_job(job_id, status="analyzing")
        log(job_id, "Asking Gemini which moments will pop off...")
        picks = pick_viral_segments(segments)

        update_job(job_id, status="cutting")
        clips = []
        for i, pick in enumerate(picks):
            log(job_id, f"Cutting clip {i+1}/{len(picks)}: {pick['title']}")
            clip_filename = f"{job_id}_{i}.mp4"
            clip_path = CLIP_DIR / clip_filename
            srt_path = CLIP_DIR / f"{job_id}_{i}.srt"

            write_srt_for_range(segments, pick["start"], pick["end"], srt_path)
            cut_and_subtitle(src_path, pick["start"], pick["end"], srt_path, clip_path)

            clips.append({
                "file": clip_filename,
                "title": pick["title"],
                "description": pick.get("description", ""),
                "start": pick["start"],
                "end": pick["end"],
                "reason": pick.get("reason", ""),
                "published": False,
            })
            update_job(job_id, clips=clips)

        update_job(job_id, status="done")
        log(job_id, "All clips ready for review.")
    except Exception as e:
        update_job(job_id, status="error")
        log(job_id, f"ERROR: {e}")


def transcribe(src_path):
    """Returns a list of {start, end, text} word/phrase-level segments."""
    from faster_whisper import WhisperModel

    model_size = os.environ.get("WHISPER_MODEL", "small")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    raw_segments, _info = model.transcribe(src_path, word_timestamps=True)
    segments = []
    for seg in raw_segments:
        segments.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
    return segments


def pick_viral_segments(segments):
    """Ask Gemini to choose the best short clips from the transcript."""
    transcript_text = "\n".join(
        f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments
    )

    if not GEMINI_API_KEY:
        # Fallback with no API key: just chunk the video evenly so the
        # pipeline still produces something to review.
        return fallback_even_chunks(segments)

    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = f"""You are a short-form video editor who finds the clips most likely
to go viral on YouTube Shorts / TikTok. Below is a timestamped transcript.

Pick the {NUM_CLIPS} best standalone moments. Each clip must be under
{MAX_CLIP_SECONDS} seconds and make sense without the rest of the video
(a hook, a punchline, a surprising fact, a strong emotional beat, etc).

Respond ONLY with a JSON array, no markdown fences, no commentary:
[
  {{"start": 12.3, "end": 34.5, "title": "short punchy title", "description": "1-2 sentence description", "reason": "why this will perform well"}}
]

Transcript:
{transcript_text}
"""
    response = model.generate_content(prompt)
    text = response.text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        picks = json.loads(text)
    except json.JSONDecodeError:
        picks = fallback_even_chunks(segments)
    return picks


def fallback_even_chunks(segments):
    if not segments:
        return []
    total_end = segments[-1]["end"]
    chunk_len = min(MAX_CLIP_SECONDS, max(15, total_end / max(NUM_CLIPS, 1)))
    picks = []
    t = 0.0
    i = 0
    while t < total_end and i < NUM_CLIPS:
        picks.append({
            "start": t,
            "end": min(t + chunk_len, total_end),
            "title": f"Clip {i+1}",
            "description": "",
            "reason": "even chunk (no GEMINI_API_KEY set)",
        })
        t += chunk_len
        i += 1
    return picks


def write_srt_for_range(segments, start, end, out_path):
    def fmt(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    lines = []
    idx = 1
    for seg in segments:
        if seg["end"] < start or seg["start"] > end:
            continue
        rel_start = max(seg["start"] - start, 0)
        rel_end = min(seg["end"] - start, end - start)
        if rel_end <= rel_start:
            continue
        lines.append(str(idx))
        lines.append(f"{fmt(rel_start)} --> {fmt(rel_end)}")
        lines.append(seg["text"])
        lines.append("")
        idx += 1

    with open(out_path, "w") as f:
        f.write("\n".join(lines))


SHORTS_WIDTH = int(os.environ.get("SHORTS_WIDTH", "1080"))
SHORTS_HEIGHT = int(os.environ.get("SHORTS_HEIGHT", "1920"))


def cut_and_subtitle(src_path, start, end, srt_path, out_path):
    """Cut the given range and export it as a proper 9:16 YouTube Short:
    a blurred, filled copy of the frame as background, the original footage
    centered on top (nothing cropped off), and subtitles burned in — all in
    a single ffmpeg pass."""
    duration = end - start
    w, h = SHORTS_WIDTH, SHORTS_HEIGHT

    style = "FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3"
    filter_complex = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},gblur=sigma=20[bg2];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,"
        f"subtitles={srt_path}:force_style='{style}'[v]"
    )

    subprocess.run([
        "ffmpeg", "-y", "-ss", str(start), "-i", str(src_path), "-t", str(duration),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-c:a", "aac",
        str(out_path),
    ], check=True)


# ---------------------------------------------------------------------------
# YouTube publishing (OAuth)
# ---------------------------------------------------------------------------

YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


@app.route("/auth/youtube")
def auth_youtube():
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_PATH), scopes=YT_SCOPES,
        redirect_uri=url_for("auth_youtube_callback", _external=True),
    )
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    session["yt_state"] = state
    return redirect(auth_url)


@app.route("/auth/youtube/callback")
def auth_youtube_callback():
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_PATH), scopes=YT_SCOPES,
        redirect_uri=url_for("auth_youtube_callback", _external=True),
        state=session.get("yt_state"),
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    with open(TOKENS_DIR / "youtube_token.json", "w") as f:
        f.write(creds.to_json())
    return redirect(url_for("index"))


def get_youtube_client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_path = TOKENS_DIR / "youtube_token.json"
    if not token_path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path), YT_SCOPES)
    return build("youtube", "v3", credentials=creds)


@app.route("/api/publish", methods=["POST"])
def publish():
    data = request.get_json()
    job_id = data["job_id"]
    clip_index = data["clip_index"]

    job = load_job(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404
    clip = job["clips"][clip_index]

    yt = get_youtube_client()
    if yt is None:
        return jsonify({"error": "not connected to YouTube — visit /auth/youtube first"}), 400

    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": clip["title"][:100],
            "description": clip.get("description", ""),
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(CLIP_DIR / clip["file"]), chunksize=-1, resumable=True)
    request_yt = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request_yt.execute()

    clip["published"] = True
    clip["youtube_id"] = response.get("id")
    save_job(job)

    return jsonify({"youtube_id": response.get("id")})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)

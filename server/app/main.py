"""DUBBER AI PRO — server backend (Phase 1).

The Android app is only a client: it uploads a video, edits segments,
and downloads the result. Everything heavy runs here:
  Gemini transcription  •  Gemini/Google translation
  Edge TTS dubbing     •  ffmpeg export (mix + stream-copy video)

Job model (mirrors the desktop table):
  POST /jobs/transcribe  -> {job_id}          (upload video, background job)
  GET  /jobs/{id}        -> status + segments
  POST /jobs/{id}/translate {target, engine}  (segments -> translated)
  POST /jobs/{id}/tts     {voice, pitch, rate} (per-segment Edge TTS)
  POST /jobs/{id}/export  {background_volume} (dubbed mp4)
  GET  /jobs/{id}/download?type=video|srt|tts_zip

Auth for Phase 1 is deliberately simple: the server is YOUR server.
Run it behind a host that requires login, or put it behind a reverse
proxy with basic auth. The Gemini key is taken from the GEMINI_API_KEY
env var first; a key sent by the phone (X-Gemini-Key header) is used
only for that one job and is never written to disk or logs.

Phase 2 (not in this file yet, endpoints return 501 with a roadmap):
  blur / text / logo overlays, burned subtitles, Demucs vocal
  separation, lip-sync, batch processing. Each is one more endpoint
  + one more worker function here, same job model.
"""
import asyncio
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .media import duration_seconds, extract_audio, mix_dubbed_video
from .srt_utils import format_ts, parse_srt, to_srt, validate_segments

DATA_DIR = Path(os.environ.get("DUBBER_DATA_DIR", "/data/dubber")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DUBBER AI PRO Server", version="1.0-phase1")
_jobs = {}
_lock = threading.Lock()

DEFAULT_VOICE = "km-KH-SreymomNeural"
TRANSCRIBE_PROMPT = """You are a professional audio transcriber. Listen to this audio file and create an accurate SRT subtitle file.

CRITICAL REQUIREMENTS - SPEECH ONLY:
1. Transcribe ONLY SPOKEN WORDS (dialogue, narration, speech)
2. IGNORE background music, applause, sound effects, ambient noise
3. SKIP segments with no speech
4. DO NOT write descriptions like "[Music]" or "[Applause]"
5. Preserve the ORIGINAL LANGUAGE spoken; do NOT translate
6. For Khmer audio: use correct Khmer script

TIMING ACCURACY - CRITICAL:
- Each subtitle must match exactly when the speech occurs
- Use format: 00:00:01,000 --> 00:00:03,500
- Keep each segment short (under ~10 seconds where possible)

Return ONLY the SRT file, nothing else."""


# ---------------------------------------------------------------- models
class JobOut(BaseModel):
    job_id: str
    status: str  # queued | transcribing | ready | translating | tts | exporting | done | error
    progress: str = ""
    video_name: str = ""
    duration: float = 0.0
    segments: list = []
    error: str = ""


class TranslateReq(BaseModel):
    target: str = "km"
    engine: str = "google"  # google | gemini


class TtsReq(BaseModel):
    voice: str = DEFAULT_VOICE
    pitch: str = "+0Hz"
    rate: str = "+0%"


class ExportReq(BaseModel):
    background_volume: int = 30
    tts_volume: int = 100


class SegmentsReq(BaseModel):
    segments: list


def _job_dir(job_id):
    d = DATA_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_job(video_name):
    job_id = uuid.uuid4().hex[:12]
    job = {"job_id": job_id, "status": "queued", "progress": "",
           "video_name": video_name, "duration": 0.0, "segments": [],
           "error": ""}
    with _lock:
        _jobs[job_id] = job
    return job


def _set(job_id, **kw):
    with _lock:
        _jobs[job_id].update(kw)


def _get(job_id):
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found (server restarted? jobs are in-memory in Phase 1)")
    return job


def _public(job):
    return JobOut(**{k: job[k] for k in JobOut.model_fields})


# ------------------------------------------------------------ workers
def _gemini_client(api_key):
    from google import genai
    return genai.Client(api_key=api_key)


def _transcribe_job(job_id, video_path, model, target, auto_translate, api_key):
    job_dir = _job_dir(job_id)
    try:
        _set(job_id, status="transcribing", progress="Extracting audio…")
        audio_path = job_dir / "audio.mp3"
        extract_audio(video_path, audio_path)

        if not api_key:
            raise RuntimeError(
                "No Gemini API key. Set GEMINI_API_KEY on the server, "
                "or enter your key in the Android app.")
        _set(job_id, progress="Uploading audio to Gemini…")
        client = _gemini_client(api_key)
        audio_file = client.files.upload(file=str(audio_path))
        deadline = time.monotonic() + 600
        while getattr(audio_file.state, "name", "") == "PROCESSING":
            if time.monotonic() >= deadline:
                raise TimeoutError("Gemini timed out processing the audio")
            time.sleep(2)
            audio_file = client.files.get(name=audio_file.name)
        if getattr(audio_file.state, "name", "") == "FAILED":
            raise RuntimeError("Gemini audio processing failed")

        # Same fallback chain as the desktop app: requested -> 3.6-flash
        # -> 3.5-flash -> 2.5-flash (pro requests prefer the pro model first).
        available = []
        try:
            for info in client.models.list():
                actions = list(getattr(info, "supported_actions", None) or [])
                if actions and "generateContent" not in actions:
                    continue
                name = str(getattr(info, "name", "") or "").split("models/")[-1]
                if name:
                    available.append(name)
        except Exception:
            available = []
        preferred = ([model, "gemini-3.1-pro-preview"] if "pro" in (model or "")
                     else [model]) + ["gemini-3.6-flash", "gemini-3.5-flash",
                                      "gemini-2.5-flash"]
        chosen = next((m for m in preferred if m in available), None) or model
        _set(job_id, progress=f"Transcribing with {chosen}…")
        resp = client.models.generate_content(
            model=chosen, contents=[TRANSCRIBE_PROMPT, audio_file])
        srt_text = (getattr(resp, "text", "") or "").strip()
        # Gemini sometimes wraps SRT in a code fence.
        srt_text = re.sub(r"^```(?:srt)?\s*", "", srt_text)
        srt_text = re.sub(r"\s*```$", "", srt_text)
        segments = parse_srt(srt_text)
        if not segments:
            raise RuntimeError("Gemini returned no usable subtitles")
        (job_dir / "transcript.srt").write_text(
            to_srt(segments, field="original"), encoding="utf-8")
        _set(job_id, status="ready", progress="Transcription done",
             segments=segments)
        if auto_translate and target:
            _translate_job(job_id, target, "google", api_key)
    except Exception as exc:
        _set(job_id, status="error", error=str(exc)[:1000])


def _translate_job(job_id, target, engine, api_key):
    job = _get(job_id)
    segments = job["segments"]
    if not segments:
        raise HTTPException(400, "No segments to translate yet")
    _set(job_id, status="translating", progress=f"Translating to {target}…")
    lang_map = {"km": "km", "en": "en", "zh": "zh-CN", "ja": "ja",
                "ko": "ko", "th": "th", "vi": "vi"}
    code = lang_map.get(target, target)
    try:
        if engine == "gemini" and api_key:
            client = _gemini_client(api_key)
            texts = [s["original"] for s in segments]
            prompt = ("Translate each line below to the target language, "
                      "keeping the same order and one line per input line. "
                      "Return ONLY the translations, one per line.\n\n"
                      + "\n".join(texts))
            resp = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt)
            out = (getattr(resp, "text", "") or "").splitlines()
            for seg, line in zip(segments, out):
                seg["translated"] = line.strip()
        else:
            from deep_translator import GoogleTranslator
            tr = GoogleTranslator(source="auto", target=code)
            for seg in segments:
                if seg["original"].strip():
                    seg["translated"] = tr.translate(seg["original"])
        _set(job_id, status="ready", progress="Translation done",
             segments=segments)
    except Exception as exc:
        _set(job_id, status="error", error=str(exc)[:1000])
        raise


async def _tts_segment(text, voice, pitch, rate, out_path):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, pitch=pitch, rate=rate)
    await communicate.save(str(out_path))


def _tts_job(job_id, voice, pitch, rate):
    job = _get(job_id)
    job_dir = _job_dir(job_id)
    tts_dir = job_dir / "tts"
    tts_dir.mkdir(exist_ok=True)
    _set(job_id, status="tts", progress="Generating voices…")
    try:
        for seg in job["segments"]:
            text = (seg.get("translated") or seg.get("original") or "").strip()
            if not text:
                continue
            out = tts_dir / f"seg_{seg['index']:04d}.mp3"
            asyncio.run(_tts_segment(
                text, seg.get("voice") or voice,
                seg.get("pitch") or pitch, seg.get("rate") or rate, out))
            seg["tts_file"] = out.name
        _set(job_id, status="ready", progress="TTS done",
             segments=job["segments"])
    except Exception as exc:
        _set(job_id, status="error", error=str(exc)[:1000])


def _export_job(job_id, background_volume, tts_volume):
    job = _get(job_id)
    job_dir = _job_dir(job_id)
    _set(job_id, status="exporting", progress="Mixing dubbed video…")
    try:
        tts_dir = job_dir / "tts"
        clips = []
        for seg in job["segments"]:
            name = seg.get("tts_file")
            if name and (tts_dir / name).exists():
                clips.append((float(seg["start"]), str(tts_dir / name)))
        if not clips:
            raise RuntimeError("No TTS audio — run TTS before export")
        out = job_dir / "dubbed.mp4"
        mix_dubbed_video(
            job_dir / "input.mp4", clips, out,
            original_volume=max(0, min(100, background_volume)) / 100.0,
            tts_volume=max(0, min(200, tts_volume)) / 100.0)
        (job_dir / "dubbed.srt").write_text(
            to_srt(job["segments"]), encoding="utf-8")
        _set(job_id, status="done", progress="Export done")
    except Exception as exc:
        _set(job_id, status="error", error=str(exc)[:1000])


# ------------------------------------------------------------ endpoints
@app.get("/health")
def health():
    return {"ok": True, "service": "dubber-ai-pro-server", "phase": 1}


@app.post("/jobs/transcribe", response_model=JobOut)
async def transcribe(file: UploadFile = File(...),
                     model: str = "gemini-3.6-flash",
                     target: str = "km",
                     auto_translate: bool = True,
                     gemini_key: str = Header(default="", alias="X-Gemini-Key")):
    job = _new_job(file.filename or "video.mp4")
    job_dir = _job_dir(job["job_id"])
    video_path = job_dir / "input.mp4"
    with open(video_path, "wb") as fh:
        shutil.copyfileobj(file.file, fh)
    job["duration"] = duration_seconds(video_path)
    api_key = gemini_key.strip() or os.environ.get("GEMINI_API_KEY", "")
    threading.Thread(target=_transcribe_job, args=(
        job["job_id"], video_path, model, target, auto_translate, api_key),
        daemon=True).start()
    return _public(_get(job["job_id"]))


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str):
    return _public(_get(job_id))


@app.post("/jobs/{job_id}/segments", response_model=JobOut)
def set_segments(job_id: str, req: SegmentsReq):
    """Save the phone's edited segment table (the core editing step)."""
    cleaned, errors = validate_segments(req.segments)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    _set(job_id, segments=cleaned, status="ready", progress="Segments saved")
    return _public(_get(job_id))


@app.post("/jobs/{job_id}/translate", response_model=JobOut)
def translate(job_id: str, req: TranslateReq,
              gemini_key: str = Header(default="", alias="X-Gemini-Key")):
    api_key = gemini_key.strip() or os.environ.get("GEMINI_API_KEY", "")
    threading.Thread(target=_translate_job, args=(
        job_id, req.target, req.engine, api_key), daemon=True).start()
    return _public(_get(job_id))


@app.post("/jobs/{job_id}/tts", response_model=JobOut)
def tts(job_id: str, req: TtsReq):
    threading.Thread(target=_tts_job, args=(
        job_id, req.voice, req.pitch, req.rate), daemon=True).start()
    return _public(_get(job_id))


@app.post("/jobs/{job_id}/export", response_model=JobOut)
def export(job_id: str, req: ExportReq):
    threading.Thread(target=_export_job, args=(
        job_id, req.background_volume, req.tts_volume), daemon=True).start()
    return _public(_get(job_id))


@app.get("/jobs/{job_id}/download")
def download(job_id: str, type: str = "video"):
    job_dir = _job_dir(job_id)
    if type == "video":
        path = job_dir / "dubbed.mp4"
        name = "dubbed.mp4"
    elif type == "srt":
        path = job_dir / "dubbed.srt"
        name = "dubbed.srt"
    elif type == "tts_zip":
        path = job_dir / "tts.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for f in sorted((job_dir / "tts").glob("*.mp3")):
                zf.write(f, f.name)
        name = "tts.zip"
    else:
        raise HTTPException(400, "type must be video, srt or tts_zip")
    if not path.exists():
        raise HTTPException(404, f"{type} not ready yet — run that step first")
    return FileResponse(path, filename=name)


@app.get("/jobs/{job_id}/phase2/{feature}")
def phase2_roadmap(job_id: str, feature: str):
    """Phase 2 features are not built yet — honest 501, not a fake success."""
    _get(job_id)
    roadmap = {
        "blur": "Blur region overlay (ffmpeg boxblur + enable between timestamps)",
        "text": "Text overlay (ffmpeg drawtext, Khmer font required on server)",
        "logo": "Logo/image overlay (ffmpeg overlay, per-layer geometry)",
        "burn_subtitle": "Burned subtitles (ffmpeg subtitles filter, ASS styling)",
        "demucs": "Vocal separation (Demucs + torch on a GPU server — needs a bigger host)",
        "lipsync": "Lip-sync (needs the desktop LipSyncWorker model — heaviest Phase 2 item)",
        "batch": "Batch: same pipeline queued over many videos, merged outputs",
    }
    if feature not in roadmap:
        raise HTTPException(404, "Unknown Phase 2 feature")
    raise HTTPException(501, f"Phase 2 — not built yet: {roadmap[feature]}")

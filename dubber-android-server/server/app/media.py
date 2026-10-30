"""ffmpeg helpers. The server, not the phone, runs all of these.

Requires ffmpeg + ffprobe on PATH (see Dockerfile — apt installs both).
"""
import json
import subprocess


def run(cmd, timeout=1800):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg failed").strip()[-2000:])
    return proc


def probe(path):
    proc = run(["ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", "-show_format", str(path)])
    return json.loads(proc.stdout or "{}")


def has_audio(path):
    try:
        return any(s.get("codec_type") == "audio"
                   for s in probe(path).get("streams", []))
    except Exception:
        return False


def extract_audio(video_path, audio_path):
    """Extract a Gemini-friendly audio track (mp3, mono-ish, 16k)."""
    run(["ffmpeg", "-y", "-i", str(video_path), "-vn",
         "-ac", "1", "-ar", "16000", "-b:a", "128k", str(audio_path)])


def duration_seconds(path):
    try:
        return float(probe(path).get("format", {}).get("duration") or 0)
    except Exception:
        return 0.0


def mix_dubbed_video(video_path, tts_files, output_path,
                     original_volume=0.30, tts_volume=1.0):
    """Mix per-segment TTS clips over the original audio at their times.

    tts_files: list of (start_seconds, clip_path). Each clip is delayed
    to its segment start, then amixed with the original track (turned
    down to original_volume, as in the desktop export: background 30%
    by default). Video is stream-copied, never re-encoded.
    """
    if not tts_files:
        raise ValueError("No TTS audio to mix — generate TTS first")
    inputs = ["-i", str(video_path)]
    for _, clip in tts_files:
        inputs += ["-i", str(clip)]

    filt = []
    # Original audio, turned down. Input 0 is the video file.
    if has_audio(video_path):
        filt.append(f"[0:a]volume={original_volume:.2f}[a0]")
        mix_inputs = ["[a0]"]
    else:
        mix_inputs = []
    for i, (start, _) in enumerate(tts_files):
        delay_ms = int(max(0.0, start) * 1000)
        filt.append(f"[{i + 1}:a]adelay={delay_ms}:all=1,"
                    f"volume={tts_volume:.2f}[a{i + 1}]")
        mix_inputs.append(f"[a{i + 1}]")
    filt.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:"
        f"normalize=0[aout]"
    )
    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", ";".join(filt),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-shortest", str(output_path),
    ]
    run(cmd, timeout=3600)

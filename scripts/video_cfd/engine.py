"""Automatic production engine of the scientific video (narration, subtitles, slides, 3D clips, editing).

A video = list of segments; a segment = narration sentences + visual clips.
  * narration: one WAV per sentence (espeak-ng + MBROLA French voice, offline) -> exact durations
  * subtitles: one SRT cue per sentence (start/end = the sentence's audio), also burned in the MP4
  * clips: 'slide' (HTML page rendered by Chromium), 'image' (PNG with slow zoom), 'frames'
    (pre-rendered 3D frames at a given fps, retimed to the clip duration)
  * each segment lasts max(narration + pauses, sum of minimum clip durations); clips share it
Output: MP4 H.264 1920x1080 25 fps + AAC, subtitles burned in, + .srt + segments.json (timeline)
"""
import json
import shutil
import subprocess
from pathlib import Path

FPS = 25
VOICE = "mb-fr1"
RATE = 152
PAUSE = 0.45


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{cmd[:3]} -> {r.stderr[-800:]}")
    return r


def dur(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout)


def tts(text, out):
    wav = Path(out)
    run(["espeak-ng", "-v", VOICE, "-s", str(RATE), "-w", str(wav), text])
    run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-ar", "48000", "-ac", "2", str(wav.with_suffix(".n.wav"))])
    wav.with_suffix(".n.wav").replace(wav)
    return dur(wav)


def srt_time(t):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def still_clip(png, seconds, out, zoom=True):
    n = max(1, int(round(seconds * FPS)))
    vf = (f"scale=2112:1188,zoompan=z='min(1+0.0006*on,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s=1920x1080:fps={FPS}"
          if zoom else f"scale=1920:1080,fps={FPS}")
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png), "-vf", vf, "-frames:v", str(n),
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(out)])


def frames_clip(pattern_dir, src_fps, seconds, out):
    files = sorted(Path(pattern_dir).glob("*.png"))
    lst = Path(out).with_suffix(".txt")
    lst.write_text("".join(f"file '{f}'\nduration {1 / src_fps:.5f}\n" for f in files) + f"file '{files[-1]}'\n")
    natural = len(files) / src_fps
    speed = natural / seconds if seconds > 0 else 1.0
    vf = f"setpts={1 / speed:.5f}*PTS,minterpolate=fps={FPS}:mi_mode=blend,scale=1920:1080" if src_fps < FPS else f"setpts={1 / speed:.5f}*PTS,fps={FPS},scale=1920:1080"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf, "-t", f"{seconds:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(out)])


def build(segments, work, out_mp4, slide_renderer):
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    timeline, cues, t0 = [], [], 0.0
    seg_videos, seg_audios = [], []
    for si, seg in enumerate(segments):
        sd = work / f"s{si:02d}_{seg['id']}"; sd.mkdir(exist_ok=True)
        # narration
        parts, t = [], 0.6
        for k, sentence in enumerate(seg["narration"]):
            w = sd / f"n{k:02d}.wav"; d = tts(sentence, w)
            cues.append((t0 + t, t0 + t + d, sentence)); parts.append((w, t)); t += d + PAUSE
        total = max(t + 0.4, sum(c.get("min", 3.0) for c in seg["clips"]))
        # audio track of the segment (silence + sentences at their offsets)
        inputs, flt = ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"], []
        for k, (w, off) in enumerate(parts):
            inputs += ["-i", str(w)]; flt.append(f"[{k + 1}]adelay={int(off * 1000)}|{int(off * 1000)}[a{k}]")
        mix = "".join(f"[a{k}]" for k in range(len(parts)))
        flt.append(f"[0]{mix}amix=inputs={len(parts) + 1}:normalize=0,atrim=0:{total:.3f}[out]")
        sa = sd / "audio.wav"
        run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(flt), "-map", "[out]", str(sa)])
        # visual clips: minimum durations, the rest shared proportionally
        mins = [c.get("min", 3.0) for c in seg["clips"]]; extra = total - sum(mins)
        weights = [c.get("weight", 1.0) for c in seg["clips"]]; wsum = sum(weights)
        cv = []
        for ci, c in enumerate(seg["clips"]):
            dsec = mins[ci] + extra * weights[ci] / wsum
            o = sd / f"c{ci:02d}.mp4"
            if c["type"] == "slide":
                png = sd / f"c{ci:02d}.png"; slide_renderer(c["html"], png); still_clip(png, dsec, o, zoom=c.get("zoom", False))
            elif c["type"] == "image":
                still_clip(c["png"], dsec, o, zoom=c.get("zoom", True))
            elif c["type"] == "frames":
                frames_clip(c["dir"], c["fps"], dsec, o)
            cv.append(o)
        lst = sd / "clips.txt"; lst.write_text("".join(f"file '{p}'\n" for p in cv))
        sv = sd / "video.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(sv)])
        seg_videos.append(sv); seg_audios.append(sa)
        timeline.append({"id": seg["id"], "title": seg.get("title", ""), "start": t0, "duration": total,
                         "clips": [c.get("label", c["type"]) for c in seg["clips"]]})
        t0 += total
        print(f"segment {seg['id']}: {total:.1f} s")
    # concat
    (work / "v.txt").write_text("".join(f"file '{p}'\n" for p in seg_videos))
    (work / "a.txt").write_text("".join(f"file '{p}'\n" for p in seg_audios))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(work / "v.txt"), "-c", "copy", str(work / "all_v.mp4")])
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(work / "a.txt"), "-c", "pcm_s16le", str(work / "all_a.wav")])
    srt = Path(out_mp4).with_suffix(".srt")
    srt.write_text("".join(f"{i + 1}\n{srt_time(a)} --> {srt_time(b)}\n{txt}\n\n" for i, (a, b, txt) in enumerate(cues)), encoding="utf-8")
    style = "FontName=DejaVu Sans,FontSize=12,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=26"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(work / "all_v.mp4"), "-i", str(work / "all_a.wav"),
         "-vf", f"subtitles={srt}:force_style='{style}'", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(out_mp4)])
    Path(out_mp4).with_suffix(".timeline.json").write_text(json.dumps(timeline, indent=1, ensure_ascii=False))
    return t0

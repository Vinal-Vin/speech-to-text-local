"""Phase 4.5: compare ASR backends on YOUR hardware and YOUR speech.

Leaderboard averages are measured on read audiobook speech. Your dictation is
technical instructions full of identifiers and library names, on your CPU. The
only measurement that matters is the one you take yourself.

Usage:
    # 1. Record a handful of real utterances (or drop your own WAVs in ./samples)
    python scripts/benchmark.py record --count 10

    # 2. Point it at each backend in turn and compare
    docker compose up -d                       # ASR_BACKEND=parakeet
    python scripts/benchmark.py run --label parakeet

    ASR_BACKEND=whisper docker compose up -d   # needs INSTALL_WHISPER=1
    python scripts/benchmark.py run --label whisper

    # 3. See the results side by side
    python scripts/benchmark.py report
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
RESULTS = ROOT / "samples" / "results.json"
SERVER = "http://127.0.0.1:8760"


def cmd_record(args: argparse.Namespace) -> int:
    """Record utterances to ./samples so every backend sees identical audio."""
    import numpy as np
    import sounddevice as sd

    SAMPLES.mkdir(exist_ok=True)
    sys.path.insert(0, str(ROOT / "client"))
    from stt_client.recorder import to_wav_bytes

    print(
        f"Recording {args.count} utterances of up to {args.seconds:.0f}s each.\n"
        "Say the kind of thing you actually dictate -- file names, library\n"
        "names, code instructions. Press Enter to start each one.\n"
    )
    for i in range(1, args.count + 1):
        input(f"  [{i}/{args.count}] Enter to record...")
        rec = sd.rec(int(args.seconds * 16000), samplerate=16000, channels=1, dtype="float32")
        print("    recording... (Enter to stop early)")
        sd.wait()
        audio = rec.reshape(-1)
        # Trim trailing silence so the file is not padded to the full length.
        nz = np.where(np.abs(audio) > 0.01)[0]
        if len(nz):
            audio = audio[: min(len(audio), nz[-1] + 8000)]
        path = SAMPLES / f"sample_{i:02d}.wav"
        path.write_bytes(to_wav_bytes(audio, 16000))
        print(f"    saved {path.name} ({len(audio) / 16000:.1f}s)")

    print(f"\nDone. {args.count} samples in {SAMPLES}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    import httpx

    wavs = sorted(SAMPLES.glob("*.wav"))
    if not wavs:
        print(f"No samples in {SAMPLES}. Run:  python scripts/benchmark.py record", file=sys.stderr)
        return 1

    try:
        health = httpx.get(f"{SERVER}/health", timeout=10).json()
    except Exception as exc:
        print(f"Cannot reach the server at {SERVER}: {exc}", file=sys.stderr)
        return 1

    label = args.label or health.get("backend", "unknown")
    print(f"Benchmarking '{label}'  (server reports: {health.get('backend')})")
    print(f"{len(wavs)} samples\n")

    rows = []
    with httpx.Client(timeout=300.0) as client:
        for wav in wavs:
            data = wav.read_bytes()
            t0 = time.perf_counter()
            r = client.post(f"{SERVER}/transcribe", files={"file": (wav.name, data, "audio/wav")})
            wall_ms = (time.perf_counter() - t0) * 1000
            r.raise_for_status()
            p = r.json()
            rows.append(
                {
                    "sample": wav.name,
                    "duration_s": p["duration_s"],
                    "wall_ms": round(wall_ms),
                    "server_ms": p["latency_ms"],
                    "text": p["text"],
                }
            )
            # Real-time factor: <1.0 means faster than the audio is long.
            rtf = (wall_ms / 1000) / max(p["duration_s"], 1e-6)
            print(f"  {wav.name}  {p['duration_s']:5.1f}s  {wall_ms:6.0f}ms  rtf={rtf:.2f}")
            print(f"      {p['text'][:100]}")

    all_results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    all_results[label] = {
        "backend": health.get("backend"),
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows": rows,
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(all_results, indent=2))

    median = statistics.median(r["wall_ms"] for r in rows)
    print(f"\n  median {median:.0f}ms over {len(rows)} samples -> saved as '{label}'")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    if not RESULTS.exists():
        print("No results yet. Run:  python scripts/benchmark.py run", file=sys.stderr)
        return 1
    data = json.loads(RESULTS.read_text())

    print(f"\n{'backend':<16} {'median':>9} {'mean':>9} {'max':>9} {'median RTF':>11}")
    print("-" * 58)
    for label, entry in data.items():
        rows = entry["rows"]
        walls = [r["wall_ms"] for r in rows]
        rtfs = [(r["wall_ms"] / 1000) / max(r["duration_s"], 1e-6) for r in rows]
        print(
            f"{label:<16} {statistics.median(walls):>8.0f}ms {statistics.mean(walls):>8.0f}ms "
            f"{max(walls):>8.0f}ms {statistics.median(rtfs):>11.2f}"
        )

    print("\nRTF < 1.0 means transcription is faster than real time.")
    print("For push-to-talk, median latency is what you actually feel.\n")

    # Transcripts side by side: latency is only half the decision, and
    # differences on technical vocabulary are what usually decide it.
    labels = list(data)
    if len(labels) > 1:
        print("Transcripts (compare accuracy on technical terms):\n")
        n = min(len(data[l]["rows"]) for l in labels)
        for i in range(n):
            print(f"  --- {data[labels[0]]['rows'][i]['sample']} ---")
            for label in labels:
                print(f"    {label:<12} {data[label]['rows'][i]['text']}")
            print()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="record dictation samples")
    rec.add_argument("--count", type=int, default=10)
    rec.add_argument("--seconds", type=float, default=10.0)
    rec.set_defaults(func=cmd_record)

    run = sub.add_parser("run", help="benchmark the currently running backend")
    run.add_argument("--label", help="name for this run (defaults to the reported backend)")
    run.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="compare all recorded runs")
    rep.set_defaults(func=cmd_report)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

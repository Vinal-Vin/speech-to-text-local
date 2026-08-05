# speech-to-text-local

Local push-to-talk dictation for Windows. Hold a key, speak, release — your
words appear at the cursor in whatever app has focus. A self-hosted equivalent
of Wispr Flow or Handy.

Everything runs on your machine. No audio ever leaves it.

Built for driving agentic coding work by voice instead of typing.

---

## How it works

Docker Desktop on Windows runs containers inside a WSL2/Hyper-V VM, and that
VM has **no microphone passthrough** and **no way to inject keystrokes** into
Windows apps. So the system is split in two — this is forced by the platform,
not a preference:

```
┌──────────────── Windows host (native Python process) ─────────────────┐
│  stt-client                                                            │
│   • global push-to-talk hotkey        • clipboard + Ctrl+V insertion   │
│   • microphone capture (16 kHz mono)  • tray icon + sound cues         │
└────────────────────────────┬───────────────────────────────────────────┘
                             │  POST 127.0.0.1:8760/transcribe  (WAV)
                             │  ←  { "text": "..." }
┌────────────────────────────┴───────────────────────────────────────────┐
│  stt-server  (Docker, CPU)                                             │
│   • FastAPI   • Silero VAD   • pluggable ASR backend                   │
└────────────────────────────────────────────────────────────────────────┘
```

Docker earns its place on the server side specifically: it keeps the heavy ML
dependency tree (ONNX Runtime, CTranslate2, torch) off your Windows machine
entirely. The client stays a thin process with only prebuilt-wheel deps.

---

## Quick start

> **Run the client from Windows PowerShell — not from WSL.**
> The client needs the Windows microphone, a Windows global keyboard hook, and
> Windows `SendInput` to type into Windows apps. WSL has none of those and
> cannot send a keystroke to a Windows window, so it fails with
> `permission denied (os error 13)` — and `sudo` will not help.
>
> **Clone to the Windows filesystem** (`C:\Users\<you>\...`), not inside WSL.
> Running the client over the `\\wsl$` share is slow and causes permission errors.
>
> Only `docker compose` (the server) is fine to run from WSL.

If PowerShell refuses to run the script (`running scripts is disabled on this
system`) — common on managed devices — bypass it for that one call without
changing any machine setting:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-client.ps1
```

```powershell
# 1. Check this machine can actually run it  (see "Corporate machines" below)
python scripts\doctor.py

# 2. Install the client  (add -Embeddable if you cannot install Python)
.\scripts\setup-client.ps1

# 3. Start the ASR server. First run downloads ~600 MB of model weights.
docker compose up -d
docker compose logs -f          # wait for "backend ready"

# 4. Verify transcription without touching your cursor
cd client
python -m stt_client --print-only

# 5. Run for real
python -m stt_client

# 6. Optional: launch at sign-in (per-user Startup folder; no admin, no registry)
.\scripts\setup-client.ps1 -AddToStartup
.\scripts\setup-client.ps1 -RemoveFromStartup   # to undo
```

Hold **Right Ctrl**, speak, release. Text appears at the cursor.

---

## Corporate / managed machines — read this first

If your organisation manages your device, run `scripts\doctor.py` before
anything else. It takes a couple of minutes and tests the three things most
likely to block this project:

| Risk | What doctor.py does | If it fails |
|---|---|---|
| Microphone disabled by policy | Records 2s, reports peak amplitude | Nothing works — talk to IT |
| Global keyboard hooks blocked by endpoint security | Registers a hook, waits for a real keypress | No hotkey; you'd need to trigger manually |
| Synthetic input blocked (resembles keylogger behaviour) | Actually sends Ctrl+V into your text field | Set `insert_mode = "clipboard_only"` |

**A failure on synthetic input does not sink the project.** With
`insert_mode = "clipboard_only"` the transcript lands on your clipboard and you
press Ctrl+V yourself — you keep the whole workflow for one extra keypress.

Two more things worth knowing:

- **UIPI:** a non-elevated process cannot send input to an *elevated* window.
  If your terminal runs as administrator, insertion silently does nothing.
  This is a Windows security boundary, not a bug.
- **Corporate TLS proxy** may intercept the first-run model download from
  Hugging Face. Uncomment the proxy/CA lines in `docker-compose.yml`.

Note that Windows' own voice typing being disabled by policy is a separate,
UI-level control and does not by itself affect any of this. It is still worth
telling IT you're running a local dictation tool — much better than having it
flagged unannounced.

---

## Choosing a backend

Three ASR backends behind one interface. Swap with an env var; no code changes.

| Backend | Default model | CPU speed | Notes |
|---|---|---|---|
| **parakeet** (default) | `nemo-parakeet-tdt-0.6b-v3` | **Fastest** | Transducer decoder — no per-token autoregressive cost. Lowest WER for its size. |
| **whisper** | `small.en` | Moderate | Mature, 99 languages, easiest to debug. Needs `INSTALL_WHISPER=1`. |
| **qwen** | `Qwen/Qwen3-ASR-0.6B` | Slowest on CPU | Excellent multilingual/dialect handling. Needs `INSTALL_QWEN=1`. |
| **dummy** | — | Instant | No ML deps at all. For testing the pipeline without weights. |

```bash
# Optional backends are opt-in because they add >2 GB to the image.
docker compose build --build-arg INSTALL_WHISPER=1
ASR_BACKEND=whisper docker compose up -d
```

Each backend picks its own correct default model, so switching `ASR_BACKEND`
alone is enough.

### Why Parakeet is the default on CPU

Whisper and Qwen3-ASR are autoregressive: they run a decoder forward pass per
generated token. Parakeet's transducer decoder does not, which is why it is
dramatically faster without a GPU. Qwen3-ASR's headline figures (~92 ms TTFT,
RTF ~0.064) are measured with vLLM **on a GPU** and do not transfer to CPU.

### Docker Model Runner cannot do this

Worth stating plainly since it's a natural assumption: `docker model run`
serves `chat/completions`, `completions` and `embeddings` only — there is **no
`/v1/audio/transcriptions` endpoint**. No choice of model changes that. Qwen3-ASR
*is* usable here, but as an ordinary container we build (which is what this repo
does), not as a Model Runner model.

### Pick your own default — measure it

Leaderboards are scored on read audiobook speech. Your dictation is technical
instructions full of identifiers, on your CPU:

```bash
python scripts/benchmark.py record --count 10   # record real utterances
python scripts/benchmark.py run --label parakeet
ASR_BACKEND=whisper docker compose up -d
python scripts/benchmark.py run --label whisper
python scripts/benchmark.py report              # latency + transcripts side by side
```

---

## Configuration

Client config lives at `%APPDATA%\stt-local\config.toml`
(template: `client/config.example.toml`).

```toml
hotkey = "ctrl_r"          # hold to dictate. Also: "f9", "scroll_lock", "pause"
insert_mode = "paste"      # "paste" | "type" | "clipboard_only"
min_record_ms = 300        # ignore accidental taps
append_text = ""           # set to " " to keep dictating inline

[replacements]             # ASR reliably mis-hears the same technical terms
"get hub" = "GitHub"
"pie test" = "pytest"
```

The `[replacements]` table is worth building up as you go — it is far cheaper
than re-dictating a mangled library name.

Server config is env vars in `docker-compose.yml`: `ASR_BACKEND`, `ASR_MODEL`,
`ASR_QUANTIZATION`, `ASR_LANGUAGE`, `ASR_THREADS`, `VAD_ENABLED`.

### Insertion modes

- **`paste`** (default) — clipboard + synthetic Ctrl+V. One atomic operation,
  correct for Unicode and emoji, instant regardless of length. Restores your
  previous clipboard afterwards.
- **`type`** — synthetic per-character typing. For apps that refuse paste.
  Slow for long text.
- **`clipboard_only`** — copies only; you press Ctrl+V. Works even where
  synthetic input is blocked.

---

## Voice activity detection

Silero VAD trims silence before inference. Two reasons it matters:

1. **Latency.** Push-to-talk always captures dead air at both ends.
2. **Hallucinations.** Whisper-family models emit confident text on pure
   silence ("Thank you.", "Subtitles by …"). If VAD finds no speech at all,
   the server skips inference entirely and returns empty — so an accidental
   hotkey press inserts nothing rather than something invented.

Silero detects speech onset slightly late (measured ~0.34 s on real audio), so
the detected region is padded outwards by `vad_pad_ms` (default 300 ms) or the
first word gets clipped.

---

## Verifying

```bash
# Server, independently of the client
curl http://127.0.0.1:8760/health
curl -F "file=@sample.wav" http://127.0.0.1:8760/transcribe

# Test suite (no weights or network needed — runs on the dummy backend)
pip install pytest && pytest tests/ -v
```

**End-to-end acceptance:** open Notepad, VS Code and a browser text field in
turn. Hold the hotkey, say a sentence with a technical term and punctuation,
release. Text should appear at the cursor in all three, and your previous
clipboard contents should still be intact.

**Expected CPU latency** (10 s utterance, from key release): Parakeet
≈ 0.5–1.5 s, faster-whisper `small` int8 ≈ 2–4 s. Measure Qwen yourself.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `permission denied (os error 13)` running setup | You're in WSL. The client is Windows-only — run it from Windows PowerShell. `sudo` cannot help |
| `running scripts is disabled on this system` | `powershell -ExecutionPolicy Bypass -File .\scripts\setup-client.ps1` |
| Setup is very slow, odd permission errors | Repo cloned inside WSL; the client is reading it over `\\wsl$`. Re-clone to `C:\Users\<you>\` |
| `Cannot reach the ASR server` | `docker compose up -d`; check `docker compose logs` |
| First dictation times out | Model still downloading — watch `docker compose logs -f` |
| Text appears nowhere | Target window is elevated (UIPI), or synthetic input blocked → `clipboard_only` |
| Text appears in the wrong window | Click into the target field before releasing the hotkey |
| Hotkey does nothing | Another app owns the key, or hooks are blocked → run `doctor.py` |
| First word clipped | Raise `vad_pad_ms`, or start speaking a beat after the beep |
| Clipboard not restored | Raise `clipboard_restore_delay_ms` (some apps read it lazily) |
| Nonsense on technical terms | Add `[replacements]` entries; try a larger model |

---

## Layout

```
server/app/          FastAPI service
  backends/          parakeet | whisper | qwen_asr | dummy  (lazy-imported)
  vad.py audio.py    silence trimming, decode/resample
client/stt_client/   hotkey, recorder, transport, inject, tray
scripts/             doctor.py, benchmark.py, setup-client.ps1
tests/               pytest suite, runs without model weights
```

---

## Scope

Not included, deliberately: streaming/live-typing transcription (needs
partial-hypothesis handling and in-place text rewriting — push-to-talk with
batch insertion is what Handy and Wispr Flow actually do), GPU acceleration,
and LLM post-processing of transcripts (adds latency to the critical path).

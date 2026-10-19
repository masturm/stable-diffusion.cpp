# Video Generation Web GUI — Implementation Plan

## Overview

A standalone Python (FastAPI) web application that wraps `sd-cli` as a subprocess.
No modifications to existing C++ code. Runs on the same machine as the GPU.

```
Browser ──HTTP/WS──► FastAPI backend ──subprocess──► sd-cli
                          │
                          ├── job queue (asyncio)
                          ├── ffmpeg (frame extraction)
                          ├── workflow persistence (workflows/*.json)
                          └── model browser (models/*.gguf)
```

---

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend | Python 3.12 + FastAPI + uvicorn | async, subprocess mgmt, WebSocket |
| Frontend | Vanilla JS + HTML + CSS (no build step) | simple, served as static files |
| Video tools | ffmpeg (already installed) | extract last frame, probe duration |
| Persistence | JSON files (workflows/, history/) | human-editable, no DB needed |
| Progress | Parse sd-cli stdout (`\r` progress lines) | no C++ changes needed |

---

## Directory Structure

```
webui/
├── server.py              # FastAPI app entry point
├── config.py              # paths, defaults
├── models.py              # pydantic models (request/response schemas)
├── job_queue.py           # async job queue, subprocess runner
├── sequence.py            # video sequence orchestrator
├── model_browser.py       # scan models/ for .gguf files
├── workflow_store.py      # save/load workflow JSON files
├── history_store.py       # job history persistence
├── progress_parser.py     # parse sd-cli stdout progress
├── static/
│   ├── index.html         # single-page app
│   ├── app.js             # main frontend logic
│   ├── style.css          # styles
│   └── components/        # JS modules (prompt-seq-editor, etc.)
├── workflows/             # saved workflow JSON files
├── history/               # job history JSON files
├── outputs/               # default output directory (configurable)
└── requirements.txt       # fastapi, uvicorn, websockets
```

---

## Backend API Design

### Model Browser
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/models` | List .gguf files in models/ with size, name |

### Workflows
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/workflows` | List saved workflows |
| GET | `/api/workflows/{name}` | Load a workflow |
| POST | `/api/workflows/{name}` | Save a workflow |
| DELETE | `/api/workflows/{name}` | Delete a workflow |

### Jobs
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/jobs` | Submit a new job (single gen or sequence) |
| GET | `/api/jobs` | List all jobs (history + queue) |
| GET | `/api/jobs/{id}` | Get job status/details |
| DELETE | `/api/jobs/{id}` | Cancel a queued job |
| GET | `/api/jobs/{id}/output` | Get output file (video/image) |
| WS | `/ws/jobs/{id}` | WebSocket for real-time progress |

### History / Gallery
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/history` | List completed jobs with thumbnails |
| GET | `/api/history/{id}` | Get full job record |

---

## Job Model

```python
class Job:
    id: str                    # uuid
    type: "single" | "sequence"
    status: "queued" | "running" | "completed" | "failed" | "cancelled"
    workflow: WorkflowConfig   # full parameter snapshot
    sequence_steps: list[SequenceStep] | None
    current_step: int          # for sequences: which step is running
    progress: float            # 0.0–1.0 for current step
    log_lines: list[str]       # recent stdout lines
    outputs: list[str]         # paths to generated files
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
```

## Workflow Config (saved as JSON)

```json
{
  "name": "my_wan_workflow",
  "models": {
    "diffusion_model": "models/WAN2.2-T2V-A14B-Q8_0.gguf",
    "high_noise_diffusion_model": "models/WAN2.2-I2V-A14B-high-Q8_0.gguf",
    "vae": "models/WAN2.2_VAE.gguf",
    "clip_l": "models/clip_l.gguf",
    "t5xxl": "models/t5xxl_fp8_e4m3fn_scaled_kqv.gguf"
  },
  "generation": {
    "width": 1280,
    "height": 720,
    "video_frames": 121,
    "fps": 24,
    "steps": 30,
    "cfg_scale": 6.0,
    "high_noise_steps": 10,
    "high_noise_cfg_scale": 6.0,
    "sampler": "euler_a",
    "scheduler": "simple",
    "seed": -1,
    "output_format": "webm",
    "output_dir": "outputs/"
  },
  "flags": {
    "diffusion_fa": true,
    "vae_tiling": true,
    "unload_high_noise_model": true,
    "backend": "diffusion=ROCm2"
  },
  "env": {
    "GGML_CUDA_DISABLE_GRAPHS": "1"
  },
  "sequence": [
    {
      "prompt": "A cat walking on the beach",
      "negative_prompt": "blurry, low quality",
      "overrides": {}
    },
    {
      "prompt": "The cat starts running",
      "negative_prompt": "blurry, low quality",
      "overrides": {}
    }
  ]
}
```

---

## Video Sequence Orchestration

```python
async def run_sequence(job: Job):
    prev_video = None
    for i, step in enumerate(job.sequence_steps):
        job.current_step = i
        await notify_progress(job)

        args = build_cli_args(job.workflow, step)

        if i > 0 and prev_video:
            # Extract last frame from previous video
            last_frame = f"/tmp/{job.id}_frame_{i}.png"
            await extract_last_frame(prev_video, last_frame)
            args += ["--init-image", last_frame]

        output_path = f"{output_dir}/{job.id}_step_{i}.webm"
        args += ["--output", output_path]

        # Run sd-cli subprocess, stream progress
        success = await run_sd_cli(args, job)
        if not success:
            job.status = "failed"
            return

        prev_video = output_path
        job.outputs.append(output_path)

    job.status = "completed"
```

---

## Progress Parsing

sd-cli writes progress to stdout with `\r` (carriage return):
```
\r[==========>         ] 5/20 - 2.5it/s
```

Parse with regex: `r'\r\[.*?\]\s+(\d+)/(\d+)\s+-\s+(.+)'`

Also capture LOG_INFO lines for status messages:
- `"save result video to '...'"` → output file ready
- `"loading model..."` → loading phase
- `"unloading high noise diffusion model..."` → phase transition

WebSocket pushes JSON to frontend:
```json
{"step": 5, "total": 20, "speed": "2.5it/s", "phase": "sampling", "log": "..."}
```

---

## Frontend Design (Single Page)

### Layout
```
┌─────────────────────────────────────────────────────────┐
│  Header: [Image] [Video] [Sequences] [History] [Settings]│
├────────────────────────┬────────────────────────────────┤
│  Left Panel (form)     │  Right Panel (output/preview)  │
│                        │                                │
│  ┌─ Model Selection ─┐ │  ┌──────────────────────────┐  │
│  │ diffusion: [▼]    │ │  │  Video preview / player  │  │
│  │ high_noise: [▼]   │ │  └──────────────────────────┘  │
│  │ vae: [▼]          │ │                                │
│  │ t5xxl: [▼]        │ │  ┌──────────────────────────┐  │
│  └───────────────────┘ │  │  Progress bar            │  │
│                        │  │  [==========>    ] 5/20  │  │
│  ┌─ Parameters ──────┐ │  │  2.5 it/s               │  │
│  │ width, height     │ │  └──────────────────────────┘  │
│  │ frames, fps       │ │                                │
│  │ steps, cfg        │ │  ┌──────────────────────────┐  │
│  │ sampler, sched    │ │  │  Job log (scrollable)    │  │
│  │ flags, env        │ │  │  > loading model...      │  │
│  └───────────────────┘ │  │  > step 1/20 - 3.2it/s   │  │
│                        │  └──────────────────────────┘  │
│  ┌─ Prompts ─────────┐ │                                │
│  │ [+ Add Step]      │ │  ┌──────────────────────────┐  │
│  │ Step 1: [prompt]  │ │  │  Output gallery          │  │
│  │ Step 2: [prompt]  │ │  │  [thumb] [thumb] [thumb] │  │
│  │ Step 3: [prompt]  │ │  └──────────────────────────┘  │
│  └───────────────────┘ │                                │
│                        │                                │
│  [Save Workflow]       │                                │
│  [Load Workflow ▼]     │                                │
│  [Generate] [Cancel]   │                                │
└────────────────────────┴────────────────────────────────┘
```

### Key UI Components
1. **Model browser dropdowns** — populated from `/api/models`, grouped by type
2. **Parameter form** — all sd-cli params, organized in collapsible sections
3. **Prompt sequence editor** — dynamic list of steps, each with prompt/negative/overrides
4. **Workflow save/load** — save current form state as JSON, load from dropdown
5. **Job queue sidebar** — shows queued/running/completed jobs
6. **Progress panel** — real-time progress bar + log via WebSocket
7. **Output gallery** — thumbnails of past generations, click to view

---

## Implementation Phases

### Phase 1: Core Backend (MVP)
- [ ] `server.py` — FastAPI app, static file serving, CORS
- [ ] `config.py` — paths (sd-cli binary, models dir, output dir)
- [ ] `model_browser.py` — scan models/ for .gguf, return list with metadata
- [ ] `job_queue.py` — asyncio queue, subprocess runner, progress parsing
- [ ] `progress_parser.py` — parse stdout progress lines
- [ ] Basic API: `GET /api/models`, `POST /api/jobs`, `GET /api/jobs/{id}`
- [ ] WebSocket for progress updates

### Phase 2: Frontend (MVP)
- [ ] `index.html` + `app.js` — single page with form
- [ ] Model selection dropdowns
- [ ] Parameter form (video generation params)
- [ ] Single prompt generation (no sequences yet)
- [ ] Progress bar + log display
- [ ] Video preview for completed jobs

### Phase 3: Sequences
- [ ] `sequence.py` — sequence orchestrator
- [ ] Prompt sequence editor in UI (add/remove/reorder steps)
- [ ] Last-frame extraction via ffmpeg
- [ ] Sequence progress (step N of M)
- [ ] Per-step output tracking

### Phase 4: Workflows & History
- [ ] `workflow_store.py` — save/load JSON workflows
- [ ] `history_store.py` — persist completed job records
- [ ] Workflow save/load UI
- [ ] History gallery with thumbnails
- [ ] Output file serving

### Phase 5: Polish
- [ ] Job cancellation (kill subprocess)
- [ ] Error handling and user-friendly messages
- [ ] Responsive layout
- [ ] Keyboard shortcuts
- [ ] Dark theme

---

## Key Implementation Details

### sd-cli Invocation
```python
def build_cli_args(workflow: dict, prompt: str, negative: str, output: str) -> list[str]:
    args = [SD_CLI_PATH]
    # Models
    for key in ["diffusion_model", "high_noise_diffusion_model", "vae", "clip_l", "t5xxl"]:
        if workflow["models"].get(key):
            args += [f"--{key.replace('_', '-')}", workflow["models"][key]]
    # Parameters
    gen = workflow["generation"]
    args += ["--width", str(gen["width"]), "--height", str(gen["height"])]
    args += ["--video-frames", str(gen["video_frames"]), "--fps", str(gen["fps"])]
    args += ["--steps", str(gen["steps"]), "--cfg-scale", str(gen["cfg_scale"])]
    args += ["--prompt", prompt, "--negative-prompt", negative]
    args += ["--output", output]
    # Flags
    if workflow["flags"].get("diffusion_fa"):
        args.append("--diffusion-fa")
    if workflow["flags"].get("vae_tiling"):
        args.append("--vae-tiling")
    if workflow["flags"].get("unload_high_noise_model"):
        args.append("--unload-high-noise-model")
    if workflow["flags"].get("backend"):
        args += ["--backend", workflow["flags"]["backend"]]
    return args
```

### Subprocess Management
```python
async def run_sd_cli(args: list[str], job: Job, env: dict) -> bool:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **env},
    )
    async for line in proc.stdout:
        text = line.decode(errors="replace")
        parse_progress(text, job)
        job.log_lines.append(text)
        await notify_ws(job)
    await proc.wait()
    return proc.returncode == 0
```

### Frame Extraction
```python
async def extract_last_frame(video_path: str, output_png: str):
    # Get duration, then extract last frame
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-sseof", "-0.1", "-i", video_path,
        "-update", "1", "-frames:v", "1", output_png,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
```

---

## Dependencies

```
# requirements.txt
fastapi>=0.115
uvicorn[standard]>=0.30
websockets>=12
```

No other dependencies needed — ffmpeg is system-installed, sd-cli is the existing binary.

---

## Notes

- **Preview support**: sd-cli supports `--preview tae` (or `vae`/`proj`) with `--preview-path`.
  In a future iteration, the GUI could serve the preview file via HTTP and display it
  as a live updating image during generation.
- **Model type detection**: The model browser can detect model type from filename
  (e.g., "high" → high_noise, "VAE" → vae, "t5xxl" → text encoder) or from a
  `.meta.json` sidecar file.
- **GPU exclusivity**: The job queue ensures only one sd-cli process runs at a time.
  Queued jobs wait their turn.
- **LAN access**: Bind to `0.0.0.0:8080` by default. No auth (restricted network).

from contextlib import asynccontextmanager
from pathlib import Path
import shutil

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import history_store
import workflow_store
from config import HOST, OUTPUT_DIR, PORT, UPLOADS_DIR
from job_queue import JobQueue
from model_browser import scan_models
from models import Job, JobRequest, JobStatus, ModelInfo

job_queue = JobQueue()


@asynccontextmanager
async def lifespan(app: FastAPI):
    job_queue.start()
    yield
    job_queue.stop()


app = FastAPI(title="SD-CPP Video GUI", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Upload ---

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    dest = UPLOADS_DIR / file.filename
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"path": str(dest), "name": file.filename}


# --- Models ---

@app.get("/api/models", response_model=list[ModelInfo])
async def get_models():
    return scan_models()


# --- Workflows ---

@app.get("/api/workflows")
async def get_workflows():
    return workflow_store.list_workflows()


@app.get("/api/workflows/{name}")
async def get_workflow(name: str):
    wf = workflow_store.load_workflow(name)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    return wf


@app.post("/api/workflows/{name}")
async def put_workflow(name: str, config: dict):
    from models import WorkflowConfig
    wf = WorkflowConfig(**config)
    wf.name = name
    workflow_store.save_workflow(name, wf)
    return {"ok": True}


@app.delete("/api/workflows/{name}")
async def del_workflow(name: str):
    if not workflow_store.delete_workflow(name):
        raise HTTPException(404, "workflow not found")
    return {"ok": True}


# --- Jobs ---

@app.post("/api/jobs", response_model=Job)
async def create_job(req: JobRequest):
    job = job_queue.submit(req)
    return job


@app.get("/api/jobs", response_model=list[Job])
async def list_jobs():
    return list(job_queue.jobs.values())


@app.get("/api/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str):
    job = job_queue.jobs.get(job_id)
    if job is None:
        job = history_store.load_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str):
    ok = await job_queue.cancel(job_id)
    if not ok:
        raise HTTPException(400, "cannot cancel job")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/output/{file_index}")
async def get_job_output(job_id: str, file_index: int):
    job = job_queue.jobs.get(job_id) or history_store.load_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if file_index >= len(job.outputs):
        raise HTTPException(404, "output not found")
    path = Path(job.outputs[file_index])
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(str(path))


# --- Preview ---

@app.get("/api/jobs/{job_id}/preview")
async def get_job_preview(job_id: str):
    preview_path = OUTPUT_DIR / f"{job_id}_preview.png"
    if not preview_path.is_file():
        raise HTTPException(404, "no preview yet")
    return FileResponse(str(preview_path), media_type="image/png",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# --- History ---

@app.get("/api/history")
async def get_history():
    return history_store.list_history()


# --- WebSocket for progress ---

@app.websocket("/ws/jobs/{job_id}")
async def ws_job_progress(ws: WebSocket, job_id: str):
    await ws.accept()
    q = job_queue.subscribe(job_id)
    try:
        while True:
            msg = await q.get()
            await ws.send_json(msg)
            if msg.get("event") == "finished":
                job = job_queue.jobs.get(job_id)
                if job:
                    history_store.save_job(job)
                break
    except WebSocketDisconnect:
        pass
    finally:
        job_queue.unsubscribe(job_id, q)


# --- Combine existing videos ---

@app.post("/api/combine")
async def combine_videos(body: dict):
    import re
    filename = body.get("filename", "")
    output_dir = Path(body.get("output_dir", "")).expanduser()
    ext = body.get("output_format", "webm")

    if not filename:
        raise HTTPException(400, "filename is required")
    if not output_dir.is_dir():
        raise HTTPException(400, f"directory not found: {output_dir}")

    pattern = re.compile(rf"^{re.escape(filename)}_step(\d+)\.{re.escape(ext)}$")
    step_files: list[tuple[int, str]] = []
    for f in output_dir.iterdir():
        m = pattern.match(f.name)
        if m:
            step_files.append((int(m.group(1)), str(f)))

    if len(step_files) < 2:
        raise HTTPException(400, f"found {len(step_files)} step file(s), need at least 2")

    step_files.sort(key=lambda x: x[0])
    video_paths = [p for _, p in step_files]
    output_path = str(output_dir / f"{filename}_combined.{ext}")

    list_file = Path(output_path).with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{p}'" for p in video_paths), encoding="utf-8")

    import asyncio
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    list_file.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise HTTPException(500, "ffmpeg concat failed")

    return {"ok": True, "output": output_path, "files": video_paths}


# --- Static files ---

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)

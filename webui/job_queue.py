from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import OUTPUT_DIR, SD_CLI_PATH
from models import Job, JobRequest, JobStatus, JobType, SequenceStep
from progress_parser import detect_phase, parse_output_path, parse_progress_line


class JobQueue:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.current_process: asyncio.subprocess.Process | None = None
        self.current_job_id: str | None = None
        self._worker_task: asyncio.Task | None = None
        self._ws_subscribers: dict[str, list[asyncio.Queue]] = {}

    def start(self):
        self._worker_task = asyncio.create_task(self._worker())

    def stop(self):
        if self._worker_task:
            self._worker_task.cancel()

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._ws_subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue):
        subs = self._ws_subscribers.get(job_id, [])
        if q in subs:
            subs.remove(q)

    async def _notify(self, job: Job, event: str = "update"):
        msg = {"event": event, "job_id": job.id, "status": job.status,
               "progress": job.progress.model_dump(), "current_step": job.current_step,
               "total_steps": job.total_steps, "log": job.log_lines[-1] if job.log_lines else ""}
        for q in self._ws_subscribers.get(job.id, []):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def submit(self, req: JobRequest) -> Job:
        job = Job(
            type=req.type,
            workflow=req.workflow,
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            sequence_steps=req.sequence_steps,
            total_steps=len(req.sequence_steps) if req.sequence_steps else 1,
        )
        self.jobs[job.id] = job
        self.queue.put_nowait(job.id)
        return job

    async def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(timezone.utc).isoformat()
            await self._notify(job, "cancelled")
            return True
        if job.status == JobStatus.RUNNING and job_id == self.current_job_id:
            if self.current_process and self.current_process.returncode is None:
                self.current_process.send_signal(signal.SIGINT)
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(timezone.utc).isoformat()
            await self._notify(job, "cancelled")
            return True
        return False

    async def _worker(self):
        while True:
            job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            if not job or job.status == JobStatus.CANCELLED:
                continue
            await self._run_job(job)

    async def _run_job(self, job: Job):
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc).isoformat()
        self.current_job_id = job.id
        await self._notify(job, "started")

        try:
            if job.type == JobType.SEQUENCE and job.sequence_steps:
                await self._run_sequence(job)
            else:
                await self._run_single(job)

            if job.status == JobStatus.RUNNING:
                job.status = JobStatus.COMPLETED
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
        finally:
            job.finished_at = datetime.now(timezone.utc).isoformat()
            self.current_job_id = None
            self.current_process = None
            await self._notify(job, "finished")

    async def _run_single(self, job: Job):
        output_dir = Path(job.workflow.generation.output_dir or OUTPUT_DIR).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        ext = job.workflow.generation.output_format
        filename = job.workflow.generation.output_filename or job.id
        output_path = str(output_dir / f"{filename}.{ext}")

        # Resolve init image (from direct path or extracted from video)
        init_img = await self._resolve_init_image(job.workflow.generation, job, output_dir)

        args = self._build_args(job.workflow, job.prompt, job.negative_prompt, output_path, init_img)
        success = await self._run_cli(args, job, job.workflow.env)
        if success and output_path not in job.outputs:
            job.outputs.append(output_path)

    async def _run_sequence(self, job: Job):
        steps = job.sequence_steps or []
        output_dir = Path(job.workflow.generation.output_dir or OUTPUT_DIR).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        ext = job.workflow.generation.output_format
        filename = job.workflow.generation.output_filename or job.id
        prev_video: str | None = None

        for i, step in enumerate(steps):
            if job.status == JobStatus.CANCELLED:
                return

            job.current_step = i + 1
            job.progress.step = 0
            job.progress.total = 0
            job.progress.phase = f"sequence step {i + 1}/{len(steps)}"
            await self._notify(job)

            wf = job.workflow.model_copy(deep=True)
            if step.overrides:
                for key, val in step.overrides.items():
                    if hasattr(wf.generation, key):
                        setattr(wf.generation, key, val)

            output_path = str(output_dir / f"{filename}_step{i + 1}.{ext}")

            # Resolve init image for this step
            init_img = await self._resolve_init_image(wf.generation, job, output_dir, step_index=i)

            args = self._build_args(wf, step.prompt, step.negative_prompt, output_path, init_img)

            if i > 0 and prev_video:
                last_frame = str(output_dir / f"{filename}_frame_{i}.png")
                await self._extract_last_frame(prev_video, last_frame)
                args += ["--init-img", last_frame]

            success = await self._run_cli(args, job, wf.env)
            if not success:
                job.status = JobStatus.FAILED
                job.error = f"sequence step {i + 1} failed"
                return

            if output_path not in job.outputs:
                job.outputs.append(output_path)
            prev_video = output_path

        # Concatenate all steps into one video
        if job.workflow.generation.concat_sequence and len(job.outputs) > 1:
            concat_path = str(output_dir / f"{filename}_combined.{ext}")
            ok = await self._concat_videos(job.outputs, concat_path)
            if ok:
                job.outputs.append(concat_path)

    def _build_args(self, wf, prompt: str, negative: str, output: str, init_img: str | None = None) -> list[str]:
        args = [str(SD_CLI_PATH), "-M", "vid_gen"]

        model_map = {
            "diffusion_model": "--diffusion-model",
            "high_noise_diffusion_model": "--high-noise-diffusion-model",
            "vae": "--vae",
            "clip_l": "--clip_l",
            "t5xxl": "--t5xxl",
        }
        for field, flag in model_map.items():
            val = getattr(wf.models, field, None)
            if val:
                args += [flag, str(Path(val).expanduser())]

        gen = wf.generation
        args += ["-W", str(gen.width), "-H", str(gen.height)]
        args += ["--video-frames", str(gen.video_frames)]
        args += ["--fps", str(gen.fps)]
        args += ["--steps", str(gen.steps)]
        args += ["--cfg-scale", str(gen.cfg_scale)]
        args += ["--sampling-method", gen.sampler]
        if gen.scheduler != "default":
            args += ["--scheduler", gen.scheduler]
        if gen.seed >= 0:
            args += ["--seed", str(gen.seed)]
        if gen.high_noise_steps is not None:
            args += ["--high-noise-steps", str(gen.high_noise_steps)]
        if gen.high_noise_cfg_scale is not None:
            args += ["--high-noise-cfg-scale", str(gen.high_noise_cfg_scale)]
        if gen.flow_shift is not None:
            args += ["--flow-shift", str(gen.flow_shift)]

        args += ["-p", prompt]
        if negative:
            args += ["-n", negative]
        args += ["-o", output]

        # Init image: explicit param overrides workflow-level setting
        effective_init = init_img or (str(Path(wf.generation.init_image).expanduser()) if wf.generation.init_image else None)
        if effective_init:
            args += ["--init-img", effective_init]
        if wf.generation.end_image:
            args += ["--end-img", str(Path(wf.generation.end_image).expanduser())]

        flags = wf.flags
        if flags.diffusion_fa:
            args.append("--diffusion-fa")
        if flags.vae_tiling:
            args.append("--vae-tiling")
        if flags.vae_temporal_tiling:
            args.append("--vae-temporal-tiling")
        if flags.unload_high_noise_model:
            args.append("--unload-high-noise-model")
        if flags.backend:
            args += ["--backend", flags.backend]

        return args

    async def _run_cli(self, args: list[str], job: Job, env: dict[str, str]) -> bool:
        run_env = {**os.environ, **env}
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=run_env,
        )
        self.current_process = proc

        assert proc.stdout is not None
        buf = b""
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf or b"\r" in buf:
                idx_n = buf.find(b"\n")
                idx_r = buf.find(b"\r")
                if idx_n == -1:
                    idx = idx_r
                elif idx_r == -1:
                    idx = idx_n
                else:
                    idx = min(idx_n, idx_r)
                raw_line = buf[:idx]
                buf = buf[idx + 1:]
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                self._process_line(line, job)
                await self._notify(job)

        if buf:
            line = buf.decode(errors="replace").strip()
            if line:
                self._process_line(line, job)

        await proc.wait()
        return proc.returncode == 0

    def _process_line(self, line: str, job: Job):
        job.log_lines.append(line)
        if len(job.log_lines) > 500:
            job.log_lines = job.log_lines[-200:]

        prog = parse_progress_line(line)
        if prog:
            job.progress.step, job.progress.total, job.progress.speed = prog
            if not job.progress.phase:
                job.progress.phase = "sampling"

        out = parse_output_path(line)
        if out and out not in job.outputs:
            job.outputs.append(out)

        phase = detect_phase(line)
        if phase:
            job.progress.phase = phase

    async def _resolve_init_image(self, gen, job: Job, output_dir: Path, step_index: int = 0) -> str | None:
        """Resolve init image from init_image path or by extracting last frame from init_video.
        For sequences, only step 0 uses the workflow-level init; later steps use prev video frame."""
        if step_index > 0:
            return None
        if gen.init_video:
            video_path = str(Path(gen.init_video).expanduser())
            frame_path = str(output_dir / f"{job.id}_init_frame.png")
            await self._extract_last_frame(video_path, frame_path)
            return frame_path
        if gen.init_image:
            return str(Path(gen.init_image).expanduser())
        return None

    async def _concat_videos(self, video_paths: list[str], output_path: str) -> bool:
        list_file = Path(output_path).with_suffix('.txt')
        list_file.write_text(
            '\n'.join(f"file '{p}'" for p in video_paths), encoding='utf-8'
        )
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        list_file.unlink(missing_ok=True)
        return proc.returncode == 0

    async def _extract_last_frame(self, video_path: str, output_png: str):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-sseof", "-0.1", "-i", video_path,
            "-update", "1", "-frames:v", "1", output_png,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

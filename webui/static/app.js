const API = '';
let currentJobId = null;
let loadedWorkflowName = null;
let ws = null;
let previewInterval = null;

// --- Init ---
document.addEventListener('DOMContentLoaded', () => {
    loadModels();
    loadWorkflows();
    setupTabs();
    setupModeToggle();
    setupGenerate();
    setupCancel();
    setupWorkflowSave();
    setupSeqSteps();
    setupUploads();
    setupCombine();
});

// --- Tabs ---
function setupTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
            if (btn.dataset.tab === 'history') loadHistory();
        });
    });
}

// --- Models ---
async function loadModels() {
    const res = await fetch(`${API}/api/models`);
    const models = await res.json();
    const groups = {};
    for (const m of models) {
        (groups[m.category] ||= []).push(m);
    }
    fillSelect('model-diffusion', [...(groups.low_noise || []), ...(groups.diffusion || []), ...(groups.unknown || [])]);
    fillSelect('model-high-noise', groups.high_noise || []);
    fillSelect('model-vae', groups.vae || []);
    fillSelect('model-t5xxl', (groups.text_encoder || []).filter(m => /t5|umt5/i.test(m.name)));
    fillSelect('model-clip-l', (groups.text_encoder || []).filter(m => /clip/i.test(m.name)));
}

function fillSelect(id, items) {
    const sel = document.getElementById(id);
    const current = sel.value;
    sel.innerHTML = '<option value="">-- select --</option>';
    for (const item of items) {
        const opt = document.createElement('option');
        opt.value = item.path;
        opt.textContent = `${item.name} (${item.size_mb} MB)`;
        sel.appendChild(opt);
    }
    if (current) sel.value = current;
}

// --- Workflows ---
async function loadWorkflows() {
    const res = await fetch(`${API}/api/workflows`);
    const names = await res.json();
    const sel = document.getElementById('workflow-select');
    sel.innerHTML = '<option value="">-- Load workflow --</option>';
    for (const name of names) {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        sel.appendChild(opt);
    }
    sel.onchange = async () => {
        if (!sel.value) { loadedWorkflowName = null; document.getElementById('btn-update-wf').style.display = 'none'; return; }
        const r = await fetch(`${API}/api/workflows/${sel.value}`);
        const wf = await r.json();
        populateForm(wf);
        loadedWorkflowName = sel.value;
        document.getElementById('btn-update-wf').style.display = '';
    };
}

function populateForm(wf) {
    if (wf.models) {
        setVal('model-diffusion', wf.models.diffusion_model);
        setVal('model-high-noise', wf.models.high_noise_diffusion_model);
        setVal('model-vae', wf.models.vae);
        setVal('model-t5xxl', wf.models.t5xxl);
        setVal('model-clip-l', wf.models.clip_l);
    }
    if (wf.generation) {
        const g = wf.generation;
        setVal('gen-width', g.width);
        setVal('gen-height', g.height);
        setVal('gen-frames', g.video_frames);
        setVal('gen-fps', g.fps);
        setVal('gen-steps', g.steps);
        setVal('gen-cfg', g.cfg_scale);
        setVal('gen-hn-steps', g.high_noise_steps ?? '');
        setVal('gen-hn-cfg', g.high_noise_cfg_scale ?? '');
        setVal('gen-sampler', g.sampler);
        setVal('gen-scheduler', g.scheduler);
        setVal('gen-seed', g.seed);
        setVal('gen-flow-shift', g.flow_shift ?? '');
        setVal('gen-format', g.output_format);
        setVal('gen-output-filename', g.output_filename ?? 'output');
        setVal('gen-output-dir', g.output_dir ?? '');
        setCheck('gen-concat', g.concat_sequence ?? True);
        setVal('input-init-image', g.init_image ?? '');
        setVal('input-init-video', g.init_video ?? '');
        setVal('input-end-image', g.end_image ?? '');
    }
    if (wf.flags) {
        setCheck('flag-fa', wf.flags.diffusion_fa);
        setCheck('flag-vae-tiling', wf.flags.vae_tiling);
        setCheck('flag-vae-temporal', wf.flags.vae_temporal_tiling);
        setCheck('flag-unload-hn', wf.flags.unload_high_noise_model);
        setVal('flag-backend', wf.flags.backend ?? '');
        setVal('flag-preview', wf.flags.preview ?? '');
        setVal('flag-preview-interval', wf.flags.preview_interval ?? 5);
    }
    if (wf.env) {
        document.getElementById('env-json').value = JSON.stringify(wf.env, null, 2);
    }
    // Prompts and sequences
    if (wf.sequence_steps && wf.sequence_steps.length > 0) {
        document.querySelector('input[name="gen-mode"][value="sequence"]').checked = true;
        document.getElementById('single-prompt').style.display = 'none';
        document.getElementById('sequence-prompt').style.display = '';
        const container = document.getElementById('seq-steps');
        container.innerHTML = '';
        for (const step of wf.sequence_steps) {
            addSeqStep(step.prompt, step.negative_prompt, step.skip);
        }
    } else {
        document.querySelector('input[name="gen-mode"][value="single"]').checked = true;
        document.getElementById('single-prompt').style.display = '';
        document.getElementById('sequence-prompt').style.display = 'none';
        setVal('prompt', wf.prompt ?? '');
        setVal('neg-prompt', wf.negative_prompt ?? '');
    }
}

function setVal(id, val) { const el = document.getElementById(id); if (el) el.value = val ?? ''; }
function setCheck(id, val) { document.getElementById(id).checked = !!val; }

async function setupWorkflowSave() {
    document.getElementById('btn-save-wf').addEventListener('click', async () => {
        const name = prompt('Workflow name:');
        if (!name) return;
        const wf = collectWorkflow();
        wf.name = name;
        await fetch(`${API}/api/workflows/${name}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(wf),
        });
        loadedWorkflowName = name;
        document.getElementById('workflow-select').value = name;
        document.getElementById('btn-update-wf').style.display = '';
        loadWorkflows();
    });

    document.getElementById('btn-update-wf').addEventListener('click', async () => {
        if (!loadedWorkflowName) return;
        const wf = collectWorkflow();
        wf.name = loadedWorkflowName;
        await fetch(`${API}/api/workflows/${loadedWorkflowName}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(wf),
        });
    });
}

// --- Collect form ---
function collectWorkflow() {
    let env = {};
    try { env = JSON.parse(document.getElementById('env-json').value || '{}'); } catch(e) {}
    const mode = document.querySelector('input[name="gen-mode"]:checked').value;
    const seqSteps = collectSeqSteps();
    const wf = {
        name: 'untitled',
        models: {
            diffusion_model: val('model-diffusion') || null,
            high_noise_diffusion_model: val('model-high-noise') || null,
            vae: val('model-vae') || null,
            clip_l: val('model-clip-l') || null,
            t5xxl: val('model-t5xxl') || null,
        },
        generation: {
            width: num('gen-width'),
            height: num('gen-height'),
            video_frames: num('gen-frames'),
            fps: num('gen-fps'),
            steps: num('gen-steps'),
            cfg_scale: parseFloat(val('gen-cfg')) || 6.0,
            high_noise_steps: val('gen-hn-steps') ? num('gen-hn-steps') : null,
            high_noise_cfg_scale: val('gen-hn-cfg') ? parseFloat(val('gen-hn-cfg')) : null,
            sampler: val('gen-sampler') || 'euler_a',
            scheduler: val('gen-scheduler') || 'default',
            seed: num('gen-seed'),
            flow_shift: val('gen-flow-shift') ? parseFloat(val('gen-flow-shift')) : null,
            output_format: val('gen-format') || 'webm',
            output_filename: val('gen-output-filename') || 'output',
            output_dir: val('gen-output-dir') || null,
            concat_sequence: chk('gen-concat'),
            init_image: val('input-init-image') || null,
            end_image: val('input-end-image') || null,
            init_video: val('input-init-video') || null,
        },
        flags: {
            diffusion_fa: chk('flag-fa'),
            vae_tiling: chk('flag-vae-tiling'),
            vae_temporal_tiling: chk('flag-vae-temporal'),
            unload_high_noise_model: chk('flag-unload-hn'),
            backend: val('flag-backend') || null,
            preview: val('flag-preview') || null,
            preview_interval: num('flag-preview-interval') || null,
        },
        env,
        prompt: val('prompt'),
        negative_prompt: val('neg-prompt'),
        sequence_steps: seqSteps.length > 0 ? seqSteps : null,
    };
    return wf;
}

function val(id) { return document.getElementById(id).value.trim(); }
function num(id) { return parseInt(val(id)) || 0; }
function chk(id) { return document.getElementById(id).checked; }

// --- Combine existing steps ---
function setupCombine() {
    document.getElementById('btn-combine').addEventListener('click', async () => {
        const filename = val('gen-output-filename');
        const output_dir = val('gen-output-dir');
        const output_format = val('gen-format') || 'webm';
        if (!filename) { alert('Set an output filename first'); return; }
        if (!output_dir) { alert('Set an output directory first'); return; }

        const res = await fetch(`${API}/api/combine`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ filename, output_dir, output_format }),
        });
        if (!res.ok) {
            const err = await res.json();
            alert(err.detail || 'Combine failed');
            return;
        }
        const data = await res.json();
        alert(`Combined ${data.files.length} clips → ${data.output}`);
    });
}

// --- Uploads ---
function setupUploads() {
    bindUpload('upload-init-image', 'input-init-image');
    bindUpload('upload-init-video', 'input-init-video');
    bindUpload('upload-end-image', 'input-end-image');
}

function bindUpload(uploadId, inputId) {
    const el = document.getElementById(uploadId);
    if (!el) return;
    el.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        const res = await fetch(`${API}/api/upload`, { method: 'POST', body: form });
        const data = await res.json();
        document.getElementById(inputId).value = data.path;
    });
}

// --- Mode toggle ---
function setupModeToggle() {
    document.querySelectorAll('input[name="gen-mode"]').forEach(r => {
        r.addEventListener('change', () => {
            const seq = r.value === 'sequence' && r.checked;
            document.getElementById('single-prompt').style.display = seq ? 'none' : '';
            document.getElementById('sequence-prompt').style.display = seq ? '' : 'none';
        });
    });
}

// --- Sequence steps ---
function setupSeqSteps() {
    document.getElementById('btn-add-step').addEventListener('click', addSeqStep);
    addSeqStep();
}

function addSeqStep(prompt = '', negPrompt = '', skip = false) {
    const container = document.getElementById('seq-steps');
    const idx = container.children.length + 1;
    const div = document.createElement('div');
    div.className = 'seq-step';
    div.innerHTML = `
        <div class="step-num">Step ${idx}</div>
        <button class="remove-step" title="Remove">&times;</button>
        <label class="check seq-skip"><input type="checkbox" class="seq-skip-cb" ${skip ? 'checked' : ''}> skip (use existing)</label>
        <div class="field"><label>Prompt</label><textarea class="seq-prompt" rows="2"></textarea></div>
        <div class="field"><label>Negative Prompt</label><textarea class="seq-neg-prompt" rows="1"></textarea></div>
    `;
    div.querySelector('.seq-prompt').value = prompt;
    div.querySelector('.seq-neg-prompt').value = negPrompt;
    div.querySelector('.remove-step').addEventListener('click', () => {
        div.remove();
        renumberSteps();
    });
    container.appendChild(div);
}

function renumberSteps() {
    document.querySelectorAll('.seq-step .step-num').forEach((el, i) => {
        el.textContent = `Step ${i + 1}`;
    });
}

function collectSeqSteps() {
    const steps = [];
    document.querySelectorAll('.seq-step').forEach(div => {
        steps.push({
            prompt: div.querySelector('.seq-prompt').value.trim(),
            negative_prompt: div.querySelector('.seq-neg-prompt').value.trim(),
            overrides: {},
            skip: div.querySelector('.seq-skip-cb').checked,
        });
    });
    return steps;
}

// --- Generate ---
function setupGenerate() {
    document.getElementById('btn-generate').addEventListener('click', async () => {
        const mode = document.querySelector('input[name="gen-mode"]:checked').value;
        const wf = collectWorkflow();
        const req = { type: mode, workflow: wf };

        if (mode === 'single') {
            req.prompt = val('prompt');
            req.negative_prompt = val('neg-prompt');
        } else {
            req.sequence_steps = collectSeqSteps();
            if (req.sequence_steps.length === 0) { alert('Add at least one sequence step'); return; }
        }

        const res = await fetch(`${API}/api/jobs`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(req),
        });
        const job = await res.json();
        currentJobId = job.id;
        connectWS(job.id);
        document.getElementById('btn-generate').disabled = true;
        document.getElementById('btn-cancel').style.display = '';
        document.getElementById('log-area').textContent = '';
        document.getElementById('preview-area').innerHTML = '<p class="muted">Generating...</p>';
        startLivePreview(job.id);
    });
}

function setupCancel() {
    document.getElementById('btn-cancel').addEventListener('click', async () => {
        if (!currentJobId) return;
        await fetch(`${API}/api/jobs/${currentJobId}`, { method: 'DELETE' });
    });
}

// --- WebSocket ---
function connectWS(jobId) {
    if (ws) ws.close();
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/jobs/${jobId}`);
    ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        updateProgress(msg);
        if (msg.event === 'finished') {
            document.getElementById('btn-generate').disabled = false;
            document.getElementById('btn-cancel').style.display = 'none';
            stopLivePreview();
            loadPreview(jobId);
        }
    };
    ws.onclose = () => { ws = null; };
}

function updateProgress(msg) {
    const status = document.getElementById('job-status');
    status.textContent = msg.status;
    status.className = `status-badge ${msg.status}`;

    const p = msg.progress || {};
    const pct = p.total > 0 ? (p.step / p.total * 100) : 0;
    document.getElementById('progress-fill').style.width = `${pct}%`;
    document.getElementById('progress-text').textContent =
        p.total > 0 ? `${p.step}/${p.total} ${p.speed || ''}` : (p.phase || '');

    if (msg.total_steps > 1) {
        document.getElementById('seq-progress').style.display = '';
        document.getElementById('seq-text').textContent = `Sequence step ${msg.current_step}/${msg.total_steps}`;
    }

    if (msg.log) {
        const log = document.getElementById('log-area');
        log.textContent += msg.log + '\n';
        log.scrollTop = log.scrollHeight;
    }
}

function startLivePreview(jobId) {
    stopLivePreview();
    const previewEl = document.getElementById('live-preview');
    const imgEl = document.getElementById('live-preview-img');
    previewEl.style.display = '';
    imgEl.src = `${API}/api/jobs/${jobId}/preview?ts=${Date.now()}`;
    previewInterval = setInterval(() => {
        imgEl.src = `${API}/api/jobs/${jobId}/preview?ts=${Date.now()}`;
    }, 3000);
}

function stopLivePreview() {
    if (previewInterval) { clearInterval(previewInterval); previewInterval = null; }
    document.getElementById('live-preview').style.display = 'none';
}

async function loadPreview(jobId) {
    const res = await fetch(`${API}/api/jobs/${jobId}`);
    const job = await res.json();
    const area = document.getElementById('preview-area');
    area.innerHTML = '';
    if (job.outputs && job.outputs.length > 0) {
        for (let i = 0; i < job.outputs.length; i++) {
            const video = document.createElement('video');
            video.controls = true;
            video.src = `${API}/api/jobs/${jobId}/output/${i}`;
            area.appendChild(video);
        }
    } else {
        area.innerHTML = '<p class="muted">No output files</p>';
    }
}

// --- History ---
async function loadHistory() {
    const res = await fetch(`${API}/api/history`);
    const jobs = await res.json();
    const container = document.getElementById('history-list');
    container.innerHTML = '';
    for (const job of jobs.slice(0, 50)) {
        const card = document.createElement('div');
        card.className = 'history-card';
        const prompt = job.prompt || (job.sequence_steps?.[0]?.prompt) || '(no prompt)';
        card.innerHTML = `
            <h3>${prompt.slice(0, 60)}</h3>
            <div class="meta">${job.status} · ${job.created_at?.slice(0, 16)} · ${job.type}</div>
        `;
        if (job.outputs && job.outputs.length > 0) {
            const video = document.createElement('video');
            video.controls = true;
            video.src = `${API}/api/jobs/${job.id}/output/0`;
            card.appendChild(video);
        }
        container.appendChild(card);
    }
}

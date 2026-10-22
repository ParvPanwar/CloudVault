const $ = (id) => document.getElementById(id);
const state = { token: '', session: null, files: [], cursor: null, stats: null,
  view: 'files', filter: 'all', epoch: 0, timer: null, loading: false, jobs: [], uploading: false };
const controllers = new Set();
const statusNames = { PENDING_UPLOAD: 'Awaiting upload', QUEUED: 'Queued', PROCESSING: 'Processing', READY: 'Ready', FAILED: 'Failed' };
const inProgress = (file) => ['PENDING_UPLOAD', 'QUEUED', 'PROCESSING'].includes(file.status);
function node(tag, className = '', text = '') {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.setAttribute('aria-hidden', 'true');
  svg.append(use);
  return svg;
}
function button(label, action, className = 'button secondary', symbol) {
  const element = node('button', className);
  element.type = 'button';
  element.setAttribute('aria-label', label);
  element.title = label;
  if (symbol) element.append(icon(symbol));
  if (!className.includes('icon-button')) element.append(document.createTextNode(label));
  element.addEventListener('click', action);
  return element;
}
function bytes(value) {
  if (!Number.isFinite(value)) return '—';
  if (!value) return '0 B';
  const power = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 3);
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: power ? 1 : 0 }).format(value / 1024 ** power)} ${['B', 'KB', 'MB', 'GB'][power]}`;
}
function toast(message, error = false) {
  const item = node('div', `toast${error ? ' error' : ''}`, message);
  $('toast-region').append(item);
  setTimeout(() => item.remove(), 6500);
}
function showDialog(id) { if (!$(id).open) $(id).showModal(); }
function connection(online) {
  const element = $('connection-status');
  element.className = `connection-status ${online ? 'connected' : state.token ? 'offline' : ''}`;
  element.replaceChildren(node('span'), document.createTextNode(online ? 'Connected' : state.token ? 'Connection interrupted' : 'Not connected'));
}
async function api(path, { method = 'GET', body, token = state.token } = {}) {
  const epoch = state.epoch;
  const controller = new AbortController();
  controllers.add(controller);
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(`/api${path}`, { method, signal: controller.signal,
      headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}), cache: 'no-store' });
    const data = await response.json();
    if (epoch !== state.epoch) throw new DOMException('Workspace changed', 'AbortError');
    if (!response.ok) {
      const message = response.status === 401 ? 'Access token not accepted. Reconnect with a valid tenant token.'
        : typeof data.detail === 'string' ? data.detail : `Request failed (${response.status}). Check the file and try again.`;
      throw new Error(message);
    }
    return data;
  } finally { clearTimeout(timeout); controllers.delete(controller); }
}
function schedule() {
  clearTimeout(state.timer);
  if (!state.token || document.hidden) return;
  const active = state.files.some((file) => ['QUEUED', 'PROCESSING'].includes(file.status));
  state.timer = setTimeout(() => refresh(), active ? 10000 : 30000);
}
async function refresh(more = false) {
  if (!state.token || state.loading) return;
  state.loading = true;
  const epoch = state.epoch;
  $('refresh-button').classList.add('spinning');
  $('load-more').disabled = true;
  try {
    let files = more ? [...state.files] : [];
    let cursor = more ? state.cursor : null;
    const target = more ? files.length + 1 : Math.max(1, state.files.length);
    do {
      const page = await api(`/files?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
      files.push(...page.files);
      cursor = page.next_cursor;
    } while (cursor && files.length < target);
    const stats = await api('/stats');
    if (epoch !== state.epoch) return;
    state.files = files; state.cursor = cursor; state.stats = stats;
    $('page-error').hidden = true;
    $('live-note').replaceChildren(node('span'), document.createTextNode(`Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`));
    connection(true);
    render();
    if ($('details-dialog').open && selectedFile) {
      const current = files.find((file) => file.file_id === selectedFile);
      if (current) details(current);
    }
  } catch (error) {
    if (epoch !== state.epoch) return;
    $('page-error').textContent = error.name === 'AbortError' ? 'The request timed out. Use Refresh to try again.' : error.message;
    $('page-error').hidden = false;
    connection(false);
  } finally {
    if (epoch === state.epoch) {
      state.loading = false;
      $('refresh-button').classList.remove('spinning');
      $('load-more').disabled = false;
      schedule();
    }
  }
}
function render() {
  const stats = state.stats;
  $('nav-file-count').textContent = stats?.total_logical_files ?? '—';
  const pending = state.files.filter(inProgress).length;
  $('nav-processing-count').textContent = state.token ? `${pending}${state.cursor ? '+' : ''}` : '—';
  $('metric-files').textContent = stats?.total_logical_files ?? '—';
  $('metric-files-caption').textContent = stats ? `${stats.pending_files} awaiting upload confirmation` : 'Your private file library';
  for (const [id, key] of Object.entries({ 'metric-logical': 'logical_bytes', 'metric-physical': 'physical_bytes', 'metric-saved': 'deduplication_saved_bytes',
    'insight-logical': 'logical_bytes', 'insight-raw': 'unique_raw_bytes', 'insight-processed': 'processed_bytes', 'insight-saved': 'deduplication_saved_bytes' })) {
    $(id).textContent = bytes(stats?.[key]);
  }
  $('metric-percent').textContent = `${stats?.bandwidth_saved_percent ?? 0}%`;
  const maximum = Math.max(stats?.logical_bytes ?? 0, stats?.unique_raw_bytes ?? 0, stats?.processed_bytes ?? 0, 1);
  for (const [id, key] of [['logical', 'logical_bytes'], ['raw', 'unique_raw_bytes'], ['processed', 'processed_bytes']]) {
    $(`bar-${id}`).style.width = `${100 * (stats?.[key] ?? 0) / maximum}%`;
  }
  $('files-view').hidden = state.view === 'insights';
  $('insights-view').hidden = state.view !== 'insights';
  document.querySelector('.upload-grid').hidden = state.view === 'processing';
  const names = { files: 'All files', processing: 'Processing', insights: 'Storage insights' };
  const titles = { files: 'Your file workspace', processing: 'Your processing queue', insights: 'A lighter storage footprint' };
  $('breadcrumb-view').textContent = names[state.view];
  $('page-title').textContent = titles[state.view];
  $('page-subtitle').textContent = state.view === 'processing' ? 'Follow every file from upload to ready. Failed jobs retry automatically.' : state.view === 'insights' ? 'Real storage metrics, without the guesswork.' : 'A smarter home for your documents and images.';
  document.querySelectorAll('[data-view]').forEach((item) => {
    item.classList.toggle('active', item.dataset.view === state.view);
    if (item.dataset.view === state.view) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  });
  renderFiles();
}
function renderFiles() {
  const query = $('search-input').value.trim().toLocaleLowerCase();
  const files = state.files.filter((file) =>
    (state.view !== 'processing' || file.status !== 'READY') &&
    (state.filter === 'all' || (state.filter === 'ready' && file.status === 'READY') ||
      (state.filter === 'processing' && inProgress(file)) ||
      (state.filter === 'failed' && file.status === 'FAILED')) && file.filename.toLocaleLowerCase().includes(query));
  const hashes = new Map();
  for (const file of state.files) hashes.set(file.sha256, (hashes.get(file.sha256) ?? 0) + 1);
  $('files-title').replaceChildren(document.createTextNode(state.view === 'processing' ? 'Processing queue ' : 'All files '), node('span', 'count-pill', String(files.length)));
  $('library-subtitle').textContent = state.view === 'processing' ? 'Queued, running and failed jobs in your loaded files.' : 'Everything in your workspace, neatly in one place.';
  $('file-rows').replaceChildren(...files.map((file) => {
    const row = node('tr');
    const nameCell = node('td');
    const content = node('div', 'file-name-cell');
    const fileIcon = node('span', `file-icon ${file.content_type === 'application/pdf' ? 'pdf' : 'image'}`);
    fileIcon.append(icon(file.content_type === 'application/pdf' ? 'file' : 'image'));
    const labels = node('div');
    labels.append(button(file.filename, () => details(file), 'file-name'));
    const type = node('div', 'file-type', file.content_type === 'application/pdf' ? 'PDF document' : file.content_type === 'image/png' ? 'PNG image' : 'JPEG image');
    if (hashes.get(file.sha256) > 1) type.append(node('span', 'duplicate-note', ' · Shared original'));
    labels.append(type); content.append(fileIcon, labels); nameCell.append(content);
    const size = node('td', '', bytes(file.size_bytes));
    const status = node('td');
    const variant = { READY: 'ready', QUEUED: 'queued', PROCESSING: 'processing', FAILED: 'failed', PENDING_UPLOAD: 'pending' }[file.status];
    status.append(node('span', `status-badge status-${variant}`, statusNames[file.status]));
    const date = node('td', 'file-date', new Date(file.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }));
    date.append(node('span', 'date-time', new Date(file.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })));
    const actions = node('td');
    const group = node('div', 'row-actions');
    if (file.status !== 'PENDING_UPLOAD') group.append(button(`Download ${file.status === 'READY' ? 'processed' : 'original'}: ${file.filename}`, () => download(file, file.status === 'READY' ? 'processed' : 'original'), 'icon-button', 'download'));
    group.append(button(`Details: ${file.filename}`, () => details(file), 'icon-button', 'info'));
    actions.append(group); row.append(nameCell, size, status, date, actions);
    return row;
  }));
  $('empty-state').hidden = files.length > 0;
  $('empty-title').textContent = !state.token ? 'Your files belong here.' : state.files.length ? 'No matching files' : 'Make room for your first file.';
  $('empty-description').textContent = !state.token ? 'Connect your workspace to get started.' : state.files.length ? 'Try another search or status filter. Load more to search beyond this page.' : 'Upload a PDF or image. We’ll take care of the rest.';
  $('empty-action').hidden = Boolean(state.token && state.files.length);
  $('empty-action').replaceChildren(document.createTextNode(state.token ? 'Upload files ' : 'Connect workspace '), icon('arrow'));
  $('table-summary').textContent = state.token ? `${files.length} matching · ${state.files.length} loaded of ${state.stats?.total_logical_files ?? '…'} files` : 'No workspace connected';
  $('load-more').hidden = !state.cursor;
}
let selectedFile = null;
function details(file) {
  selectedFile = file.file_id;
  $('detail-filename').textContent = file.filename;
  const pairs = [['Status', statusNames[file.status]], ['Original size', bytes(file.size_bytes)], ['Type', file.content_type],
    ['Added', new Date(file.created_at).toLocaleString()], ['Processing attempts', `${file.attempt_count} / 3`], ['SHA-256', file.sha256]];
  if (file.status === 'READY') pairs.push(['Processed size', bytes(file.processed_size_bytes)]);
  if (file.retry_at && file.status === 'QUEUED') pairs.push(['Retry after', new Date(file.retry_at * 1000).toLocaleString()]);
  $('detail-list').replaceChildren(...pairs.flatMap(([label, value]) => [node('dt', '', label), node('dd', '', value)]));
  $('detail-error').hidden = !file.last_error;
  $('detail-error').textContent = file.last_error ?? '';
  const actions = [];
  if (file.status === 'PENDING_UPLOAD') actions.push(button('Confirm uploaded file', async () => {
    try { await api('/files/confirm-upload', { method: 'POST', body: { file_id: file.file_id } }); $('details-dialog').close(); await refresh(); }
    catch (error) { if (state.token) toast(error.message, true); }
  }));
  else actions.push(button('Original', () => download(file, 'original')));
  if (file.status === 'READY') actions.push(button('Processed', () => download(file, 'processed'), 'button primary'));
  if (file.has_thumbnail) actions.push(button('Thumbnail', () => download(file, 'thumbnail')));
  $('detail-downloads').replaceChildren(...actions);
  showDialog('details-dialog');
}
async function download(file, variant) {
  try {
    const result = await api(`/files/${file.file_id}/download?variant=${variant}`);
    const link = node('a');
    link.href = result.download_url; link.rel = 'noreferrer';
    document.body.append(link); link.click(); link.remove();
  } catch (error) { if (state.token) toast(error.message, true); }
}
function chooseFiles() { if (state.token) $('file-input').click(); else showDialog('connect-dialog'); }
function renderJobs() {
  $('upload-activity').hidden = !state.jobs.length;
  $('upload-jobs').replaceChildren(...state.jobs.map((job) => {
    const row = node('div', `upload-job${job.failed ? ' failed' : ''}`);
    const title = node('strong', '', job.name);
    const status = node('span', '', job.stage);
    const progress = node('progress'); progress.max = 100; progress.value = job.percent;
    progress.setAttribute('aria-label', `Upload progress: ${job.name}`);
    row.append(title, status, progress);
    if (job.error) row.append(node('p', '', job.error));
    if (job.confirmable) row.append(button('Retry confirmation', () => confirmJob(job), 'text-button'));
    return row;
  }));
}
async function confirmJob(job) {
  if (job.confirming || job.epoch !== state.epoch) return;
  job.confirming = true; job.confirmable = false; job.failed = false; job.error = ''; job.stage = 'Confirming upload'; renderJobs();
  try {
    const file = await api('/files/confirm-upload', { method: 'POST', body: { file_id: job.fileId } });
    job.stage = statusNames[file.status]; job.percent = 100; job.done = true;
    await refresh();
  } catch (error) {
    if (job.epoch !== state.epoch) return;
    job.failed = true; job.stage = 'Confirmation needed'; job.error = error.message;
    job.confirmable = true;
  } finally { job.confirming = false; if (job.epoch === state.epoch) renderJobs(); }
}
function putFile(url, headers, file, job) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    job.xhr = xhr;
    xhr.open('PUT', url);
    xhr.timeout = 300000;
    for (const [name, value] of Object.entries(headers)) {
      // Browsers set the signed length from the File body.
      if (name.toLowerCase() !== 'content-length') xhr.setRequestHeader(name, value);
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && job.epoch === state.epoch) {
        job.percent = Math.round(event.loaded / event.total * 95); renderJobs();
      }
    };
    xhr.onload = () => xhr.status >= 200 && xhr.status < 300 || xhr.status === 412 ? resolve() : reject(new Error(`Storage upload failed (${xhr.status}). Select the file again to renew its upload link.`));
    xhr.onerror = () => reject(new Error('Storage upload could not connect. Check your connection and the dashboard’s S3 CORS origin.'));
    xhr.ontimeout = () => reject(new Error('Upload timed out. Select the file again to retry.'));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    xhr.send(file);
  });
}
async function uploadFiles(files) {
  if (!state.token) { showDialog('connect-dialog'); return; }
  if (state.uploading) { toast('Wait for the current uploads to finish before adding more files.'); return; }
  const epoch = state.epoch;
  state.uploading = true;
  try {
    for (const file of files) {
      if (epoch !== state.epoch) break;
      const job = { name: file.name, epoch, percent: 0, stage: 'Checking file', done: false };
      state.jobs.push(job); renderJobs();
      try {
        if (!file.size || file.size > state.session.max_upload_bytes) throw new Error(`Choose a nonempty file up to ${bytes(state.session.max_upload_bytes)}.`);
        const type = file.type || ({ pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg' })[file.name.split('.').pop().toLowerCase()];
        if (!['application/pdf', 'image/png', 'image/jpeg'].includes(type)) throw new Error('Only PDF, PNG and JPEG files are supported.');
        job.stage = 'Computing SHA-256'; renderJobs();
        const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
        if (epoch !== state.epoch) break;
        const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
        job.stage = 'Checking for duplicates'; renderJobs();
        const result = await api('/files/initiate-upload', { method: 'POST', body: { filename: file.name, sha256, size_bytes: file.size, content_type: type } });
        job.fileId = result.file.file_id;
        if (result.duplicate) {
          job.stage = 'Duplicate linked · upload skipped'; job.percent = 100; job.done = true; renderJobs();
          await refresh(); continue;
        }
        job.stage = 'Uploading to private storage'; renderJobs();
        await putFile(result.upload_url, result.upload_headers, file, job);
        if (epoch !== state.epoch) break;
        await confirmJob(job);
      } catch (error) {
        if (epoch !== state.epoch) break;
        job.failed = true; job.done = true; job.stage = 'Upload interrupted'; job.error = error.message; renderJobs();
      }
    }
  } finally { if (epoch === state.epoch) { state.uploading = false; await refresh(); } }
}
function disconnect() {
  state.epoch++; clearTimeout(state.timer);
  for (const controller of controllers) controller.abort();
  for (const job of state.jobs) job.xhr?.abort();
  Object.assign(state, { token: '', session: null, files: [], cursor: null, stats: null, loading: false, jobs: [], uploading: false });
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  $('token-input').value = ''; $('account-name').textContent = 'Connect workspace'; $('avatar').textContent = 'CV';
  $('page-error').hidden = true; $('refresh-button').classList.remove('spinning');
  $('live-note').replaceChildren(node('span'), document.createTextNode('Connect to view your files'));
  connection(false); render(); renderJobs();
}
$('connect-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('connect-submit').disabled = true; $('connect-error').hidden = true;
  try {
    const token = $('token-input').value.trim();
    const session = await api('/session', { token });
    state.token = token; state.session = session; $('token-input').value = '';
    $('account-name').textContent = session.user_id; $('avatar').textContent = session.user_id.slice(0, 2).toUpperCase();
    $('account-detail').textContent = `Tenant: ${session.user_id} · Region: ${session.region}. Your token is held only in this tab’s memory.`;
    $('region-code').textContent = session.region;
    $('region-label').textContent = session.region === 'ap-southeast-2' ? 'Sydney, Australia' : 'AWS Region';
    $('upload-limits').textContent = `PDF, PNG or JPG · Up to ${bytes(session.max_upload_bytes)} per file`;
    $('connect-dialog').close(); connection(true); render(); await refresh();
  } catch (error) {
    $('connect-error').textContent = error.name === 'AbortError' ? 'Connection timed out. Try again.' : error.message;
    $('connect-error').hidden = false;
  } finally { $('connect-submit').disabled = false; }
});
$('account-button').addEventListener('click', () => showDialog(state.token ? 'account-dialog' : 'connect-dialog'));
$('disconnect-button').addEventListener('click', disconnect);
$('about-button').addEventListener('click', () => showDialog('about-dialog'));
for (const id of ['upload-button', 'browse-button', 'empty-action']) $(id).addEventListener('click', chooseFiles);
$('file-input').addEventListener('change', (event) => { const files = [...event.target.files]; event.target.value = ''; uploadFiles(files); });
$('clear-uploads').addEventListener('click', () => { state.jobs = state.jobs.filter((job) => !job.done); renderJobs(); });
$('refresh-button').addEventListener('click', () => { if (state.token) refresh(); else showDialog('connect-dialog'); });
$('load-more').addEventListener('click', () => refresh(true));
$('search-input').addEventListener('input', renderFiles);
document.querySelectorAll('[data-filter]').forEach((item) => item.addEventListener('click', () => {
  state.filter = item.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach((tab) => { tab.classList.toggle('active', tab === item); tab.setAttribute('aria-pressed', String(tab === item)); });
  renderFiles();
}));
document.querySelectorAll('[data-view]').forEach((item) => item.addEventListener('click', () => {
  state.view = item.dataset.view; state.filter = 'all';
  document.querySelectorAll('[data-filter]').forEach((tab) => { tab.classList.toggle('active', tab.dataset.filter === 'all'); tab.setAttribute('aria-pressed', String(tab.dataset.filter === 'all')); });
  $('sidebar').classList.remove('open'); $('menu-button').setAttribute('aria-expanded', 'false'); render();
}));
$('menu-button').addEventListener('click', () => { $('sidebar').classList.toggle('open'); $('menu-button').setAttribute('aria-expanded', String($('sidebar').classList.contains('open'))); });
document.querySelectorAll('[data-close]').forEach((item) => item.addEventListener('click', () => $(item.dataset.close).close()));
document.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('click', (event) => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
}));
const dropzone = $('dropzone');
for (const event of ['dragenter', 'dragover']) dropzone.addEventListener(event, (e) => { e.preventDefault(); dropzone.classList.add('dragging'); });
for (const event of ['dragleave', 'drop']) dropzone.addEventListener(event, (e) => { e.preventDefault(); dropzone.classList.remove('dragging'); });
dropzone.addEventListener('drop', (event) => uploadFiles([...event.dataTransfer.files]));
window.addEventListener('dragover', (event) => event.preventDefault());
window.addEventListener('drop', (event) => event.preventDefault());
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); else clearTimeout(state.timer); });
render();

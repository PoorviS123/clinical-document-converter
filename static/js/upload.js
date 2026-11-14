const input = document.getElementById('file-upload');
const nameLabel = document.getElementById('file-name');
const button = document.getElementById('upload-button');
const status = document.getElementById('upload-status');
const zone = document.getElementById('drop-zone');
const maxBytes = 100 * 1024 * 1024;
const allowed = ['.pdf', '.docx'];
function choose(file) {
  if (!file) { nameLabel.textContent = 'No file selected'; button.disabled = true; return; }
  const dot = file.name.lastIndexOf('.');
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
  if (!allowed.includes(ext)) { input.value=''; nameLabel.textContent='No file selected'; button.disabled=true; status.textContent='Please choose a PDF or DOCX file.'; return; }
  if (file.size > maxBytes) { input.value=''; nameLabel.textContent='No file selected'; button.disabled=true; status.textContent='File exceeds the 100 MB limit.'; return; }
  nameLabel.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
  button.disabled = false; status.textContent = '';
}
input.addEventListener('change', () => choose(input.files[0]));
['dragenter','dragover'].forEach(type => zone.addEventListener(type, e => {e.preventDefault(); zone.classList.add('drag-over');}));
['dragleave','drop'].forEach(type => zone.addEventListener(type, e => {e.preventDefault(); zone.classList.remove('drag-over');}));
zone.addEventListener('drop', e => { if(e.dataTransfer.files.length) { input.files=e.dataTransfer.files; choose(input.files[0]); }});
button.addEventListener('click', async () => {
  const file = input.files[0];
  if (!file) return;

  const data = new FormData();
  data.append('file', file);

  button.disabled = true;
  status.textContent = 'Uploading your document...';

  try {
    // Step 1: Upload the file and start the background job.
    const response = await fetch('/api/convert', {
      method: 'POST',
      body: data
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || 'Upload failed.');
    }

    const jobId = result.job_id;

    // Step 2: Keep checking until conversion finishes.
    status.textContent = 'Your document is being processed. Please wait...';

    while (true) {
      await new Promise(resolve => setTimeout(resolve, 2000));

      const statusResponse = await fetch(`/api/jobs/${jobId}/status`);
      const job = await statusResponse.json();

      if (!statusResponse.ok) {
        throw new Error(job.error || 'Could not check conversion status.');
      }

      if (job.status === 'completed') {
        status.textContent = 'All three outputs are ready. Opening results...';
        window.location.href = job.results_url;
        return;
      }

      if (job.status === 'failed') {
        throw new Error(job.message || 'Conversion failed.');
      }

      status.textContent = job.message || 'Conversion in progress...';
    }

  } catch (error) {
    status.textContent = error.message;
    button.disabled = false;
  }
});

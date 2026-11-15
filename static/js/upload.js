const input = document.getElementById('file-upload');
const nameLabel = document.getElementById('file-name');
const button = document.getElementById('upload-button');
const status = document.getElementById('upload-status');
const zone = document.getElementById('drop-zone');
const originalButtonText = button.textContent;
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
  button.textContent = 'Uploading…';
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
    button.textContent = 'Conversion running…';
    status.textContent = '⏳ Conversion is running. This can take a few minutes; results will open automatically when ready.';

    while (true) {
      await new Promise(resolve => setTimeout(resolve, 2000));

      const statusResponse = await fetch(`/api/jobs/${jobId}/status`);
      const job = await statusResponse.json();

      if (!statusResponse.ok) {
        throw new Error(job.error || 'Could not check conversion status.');
      }

      if (job.status === 'completed') {
        status.textContent = `${job.message || 'Conversion complete.'} Opening results...`;
        window.location.href = job.results_url;
        return;
      }

      if (job.status === 'failed') {
        throw new Error(job.message || 'Conversion failed.');
      }

      status.textContent = '⏳ Still working… your document is being converted. Results will open automatically when ready.';
    }

  } catch (error) {
    status.textContent = error.message;
    button.textContent = originalButtonText;
    button.disabled = false;
  }
});

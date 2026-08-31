const urlInput = document.getElementById('mediaUrl');
const pasteBtn = document.getElementById('pasteBtn');
const downloadBtn = document.getElementById('downloadBtn');
const quality = document.getElementById('quality');
const mode = document.getElementById('mode');
const statusBox = document.getElementById('status');
const result = document.getElementById('result');

function apiUrl(){
  const base = (window.APP_CONFIG?.apiBaseUrl || '').trim();
  if(!base || base.includes('YOUR-SERVICE')) throw new Error('Set your Railway URL in frontend/config.js first.');
  return base.endsWith('/') ? base : base + '/';
}

function proxyDownloadUrl(url, headers={}){
  if(!url) return '#';
  const params = new URLSearchParams();
  params.set('url', url);
  const referer = headers?.Referer || headers?.referer;
  if(referer) params.set('referer', referer);
  return `${apiUrl()}download?${params.toString()}`;
}

function setStatus(message, type=''){
  statusBox.textContent = message;
  statusBox.className = `status ${type}`.trim();
}
function clearResult(){ result.innerHTML=''; result.classList.add('hidden'); }
function safeText(v){ return String(v ?? '').replace(/[<>]/g,''); }

pasteBtn.addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    if(text) urlInput.value = text.trim();
  } catch {
    setStatus('Clipboard access was blocked. Paste the link manually.', 'error');
  }
});

function renderSingle(data){
  result.innerHTML = `
    <h2>Media ready</h2>
    <div class="mediaItem">
      <div><strong>${safeText(data.filename || 'Download')}</strong></div>
      <div style="color:#9aa4b6;font-size:13px">Response: ${safeText(data.status)}</div>
      <a class="downloadLink" href="${data.url}" target="_blank" rel="noopener noreferrer">Download file</a>
    </div>`;
  result.classList.remove('hidden');
}

function renderPicker(data){
  const items = Array.isArray(data.picker) ? data.picker : [];
  result.innerHTML = `<h2>${items.length} media item${items.length===1?'':'s'} found</h2><div class="resultGrid"></div>`;
  const grid = result.querySelector('.resultGrid');
  items.forEach((item, i) => {
    const card = document.createElement('div');
    card.className = 'mediaItem';
    if(item.thumb){
      const img = document.createElement('img');
      img.src = item.thumb; img.alt = `Media ${i+1}`; img.loading='lazy';
      card.appendChild(img);
    }
    const meta = document.createElement('div');
    meta.innerHTML = `<strong>Item ${i+1}</strong><br><span style="color:#9aa4b6;font-size:13px">${safeText(item.type || 'media')}</span>`;
    card.appendChild(meta);
    const a = document.createElement('a');
    a.className='downloadLink';
    a.href=item.downloadUrl || proxyDownloadUrl(item.url, item.headers || {});
    a.target='_blank'; a.rel='noopener noreferrer'; a.textContent='Download';
    card.appendChild(a);
    grid.appendChild(card);
  });
  if(data.audio){
    const a = document.createElement('a');
    a.className='downloadLink'; a.href=proxyDownloadUrl(data.audio); a.target='_blank'; a.rel='noopener noreferrer'; a.textContent='Download slideshow audio';
    result.appendChild(a);
  }
  result.classList.remove('hidden');
}

downloadBtn.addEventListener('click', async () => {
  clearResult();
  const mediaUrl = urlInput.value.trim();
  if(!mediaUrl){ setStatus('Paste a media link first.', 'error'); return; }
  try { new URL(mediaUrl); } catch { setStatus('Please enter a valid URL.', 'error'); return; }

  downloadBtn.disabled = true;
  downloadBtn.textContent = 'Processing…';
  setStatus('Connecting to Railway backend…');

  try {
    const response = await fetch(apiUrl(), {
      method: 'POST',
      headers: { 'Accept':'application/json', 'Content-Type':'application/json' },
      body: JSON.stringify({
        url: mediaUrl,
        videoQuality: quality.value,
        downloadMode: mode.value,
        filenameStyle: 'pretty',
        youtubeVideoCodec: 'h264',
        alwaysProxy: true,
        disableMetadata: false,
        twitterGif: true
      })
    });

    const data = await response.json().catch(() => null);
    if(!response.ok || !data) throw new Error(`Backend returned HTTP ${response.status}.`);

    if(data.status === 'error'){
      const code = data.error?.code || 'unknown_error';
      const service = data.error?.context?.service;
      throw new Error(`${service ? service + ': ' : ''}${code}`);
    }
    if(data.status === 'picker') renderPicker(data);
    else if(['download', 'redirect', 'tunnel'].includes(data.status) && data.url) renderSingle(data);
    else throw new Error(`Unsupported API response: ${data.status || 'unknown'}`);

    setStatus('Media processed successfully.', 'ok');
  } catch(err){
    setStatus(err.message || 'Could not process this link.', 'error');
  } finally {
    downloadBtn.disabled = false;
    downloadBtn.textContent = 'Get media';
  }
});

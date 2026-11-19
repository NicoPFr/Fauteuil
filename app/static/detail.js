const detailParams = new URLSearchParams(location.search);
const id = detailParams.get('id');
const previousView = detailParams.get('from') || '';
const returnHash = previousView.startsWith('#') && previousView.length <= 2048 ? previousView : '';
const el = name => document.getElementById(name);
const watch = el('watch');
const youtubeButton = el('youtubeBtn');
const keep = el('keepBtn');
const status = el('status');
const cover = el('cover');
cover.addEventListener('error', () => cover.classList.remove('ready'));
cover.addEventListener('load', () => cover.classList.add('ready'));
let item = {};

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  if (!response.ok) throw Error('Impossible de charger cette vidéo.');
  const result = await response.json();
  if (result.error) throw Error(result.error);
  return result;
}

const onlineOnlyStates = {
  is_live: 'En direct sur YouTube · indisponible hors ligne',
  is_upcoming: 'Diffusion à venir · indisponible hors ligne',
  post_live: 'Replay en préparation sur YouTube',
  unavailable: 'Téléchargement indisponible'
};

function updateAvailability() {
  const offline = item.local || item.downloaded;
  const limitation = offline ? '' : onlineOnlyStates[item.youtube_state];
  const availability = item.kept ? 'Conservée hors ligne' : offline ? 'Disponible hors ligne'
    : limitation || 'Connexion requise pour télécharger';
  el('meta').textContent = [item.channel, item.genre, item.duration ? Math.floor(item.duration / 60) + ' min' : '', availability]
    .filter(Boolean).join(' · ');
  watch.textContent = item.progress?.current > 10
    ? '▶ Reprendre à ' + Math.floor(item.progress.current / 60) + ' min'
    : '▶ Lire';
  watch.hidden = Boolean(limitation);
  youtubeButton.hidden = !limitation;
  youtubeButton.textContent = item.youtube_state === 'is_live' ? 'Regarder le direct sur YouTube' : 'Ouvrir sur YouTube';
  keep.hidden = Boolean(item.local || item.kept || limitation);
  el('removeDownloadBtn').hidden = Boolean(item.local || !item.downloaded);
}

youtubeButton.addEventListener('click', async () => {
  youtubeButton.disabled = true;
  try {
    await request('/api/youtube/open', { id });
  } catch (error) {
    status.textContent = error.message;
  } finally {
    youtubeButton.disabled = false;
  }
});

function renderDetails() {
  el('title').textContent = item.title || 'Vidéo';
  el('desc').textContent = item.description || (item.metadata_pending ? 'Chargement du synopsis…' : 'Aucune description fournie.');
  el('desc').setAttribute('aria-busy', String(Boolean(item.metadata_pending)));
  if (item.thumbnail && cover.getAttribute('src') !== item.thumbnail) {
    cover.classList.remove('ready');
    cover.src = item.thumbnail;
  }
  el('saveBtn').textContent = item.watchlisted ? '✓ Dans Ma liste' : '＋ Ma liste';
  updateAvailability();
}

async function init() {
  if (!id) throw Error('Fiche vidéo introuvable.');
  const url = '/api/details?id=' + encodeURIComponent(id);
  item = await request(url + '&preview=1');
  renderDetails();
  if (item.notice) status.textContent = item.notice;
  watch.disabled = false;
  if (!item.metadata_pending) return;

  try {
    const full = await request(url);
    for (const field of ['title', 'channel', 'description', 'duration', 'genre', 'thumbnail', 'youtube_state']) {
      if (Object.hasOwn(full, field)) item[field] = full[field];
    }
    if (full.notice && !status.textContent) status.textContent = full.notice;
  } catch (error) {
    if (!status.textContent) status.textContent = error.message;
  } finally {
    item.metadata_pending = false;
    renderDetails();
  }
}

function trackDownload() {
  const progress = el('downloadProgress');
  const controller = new AbortController();
  let timer;
  progress.hidden = false;
  progress.removeAttribute('value');

  const poll = async () => {
    try {
      const response = await fetch('/api/download/status?id=' + encodeURIComponent(id), { signal: controller.signal });
      const state = await response.json();
      if (controller.signal.aborted || !state.active) return;
      if (state.phase === 'processing') {
        status.textContent = 'Finalisation…';
        progress.value = 100;
      } else if (state.phase === 'downloading' && Number.isFinite(state.percent)) {
        status.textContent = 'Préparation de la vidéo · ' + Math.floor(state.percent) + ' %';
        progress.value = state.percent;
      } else {
        status.textContent = 'Préparation…';
        progress.removeAttribute('value');
      }
    } catch {}
    finally {
      if (!controller.signal.aborted) timer = setTimeout(poll, 1000);
    }
  };
  timer = setTimeout(poll, 400);
  return () => {
    controller.abort();
    clearTimeout(timer);
    progress.hidden = true;
  };
}

async function launch() {
  const offline = item.local || item.downloaded;
  status.textContent = offline ? 'Ouverture de la vidéo…' : 'Préparation de la vidéo…';
  watch.disabled = keep.disabled = true;
  const stopTracking = offline ? null : trackDownload();
  try {
    const result = await request('/api/play', { id, start: item.progress?.current || 0 });
    if (!result.media_url) throw Error('Le fichier vidéo est indisponible.');
    const params = new URLSearchParams({
      play: result.media_url, path: result.path, id: result.id || id, detail: id, start: item.progress?.current || 0, duration: result.duration || 0
    });
    if (detailParams.get('playlist')) { params.set('playlist', detailParams.get('playlist')); params.set('position', detailParams.get('position') || '0'); }
    location.href = '/?' + params + returnHash;
  } catch (error) {
    status.textContent = error.message;
    watch.disabled = keep.disabled = false;
    watch.textContent = 'Réessayer';
  } finally {
    stopTracking?.();
  }
}

async function keepOffline() {
  watch.disabled = keep.disabled = true;
  status.textContent = item.downloaded ? 'Enregistrement dans Hors ligne…' : 'Téléchargement pour Hors ligne…';
  const stopTracking = item.downloaded ? null : trackDownload();
  try {
    const result = await request('/api/download/keep', { id });
    item.kept = Boolean(result.kept);
    item.local = item.local || Boolean(result.local);
    item.downloaded = true;
    updateAvailability();
    status.textContent = 'Vidéo prête dans Hors ligne.';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    stopTracking?.();
    watch.disabled = keep.disabled = false;
  }
}

const removeDialog = el('removeDialog');
removeDialog.addEventListener('close', async () => {
  const confirmed = removeDialog.returnValue === 'remove';
  removeDialog.returnValue = '';
  if (!confirmed) return;
  const button = el('removeDownloadBtn');
  watch.disabled = keep.disabled = button.disabled = true;
  try {
    const result = await request('/api/download/remove', { id });
    if (!result.ok) throw Error('Impossible de retirer ce téléchargement.');
    item.downloaded = item.kept = false;
    updateAvailability();
    status.textContent = 'La copie hors ligne a été placée dans la Corbeille.';
    if (item.thumbnail?.startsWith('/api/thumb?')) {
      cover.classList.remove('ready');
      cover.removeAttribute('src');
    }
  } catch (error) {
    status.textContent = error.message;
  } finally {
    watch.disabled = keep.disabled = button.disabled = false;
  }
});

async function toggleWatchlist() {
  const button = el('saveBtn');
  if (!item.title) return;
  button.disabled = true;
  try {
    const { saved } = await request('/api/watchlist', {
      id, title: item.title, thumbnail: item.thumbnail,
      duration: item.duration, channel: item.channel, remove: Boolean(item.watchlisted)
    });
    item.watchlisted = saved;
    button.textContent = saved ? '✓ Dans Ma liste' : '＋ Ma liste';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function home() {
  if (document.referrer && new URL(document.referrer).origin === location.origin) history.back();
  else location.href = '/' + returnHash;
}
el('saveBtn').addEventListener('click', toggleWatchlist);
init().catch(error => { status.textContent = error.message; });

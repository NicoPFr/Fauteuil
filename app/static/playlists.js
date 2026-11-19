let selectedPlaylist = new URLSearchParams(location.hash.slice(1)).get('playlist') || '';
let playlistList = [];
let playlistPage = null;
let playlistNotice = '';
let playlistRequest = 0;
let playlistSyncAttempted = false;
const playlistSelection = new Set();

function playlistDetailLink(item) {
  const params = new URLSearchParams({ id: item.id, playlist: selectedPlaylist, position: item.position, from: location.hash });
  return '/detail?' + params;
}

function renderPlaylists() {
  const message = playlistNotice ? `<p class="status" role="status">${esc(playlistNotice)}</p>` : '';
  if (selectedPlaylist && playlistPage) {
    const items = playlistPage.items || [];
    return `<section class="playlist-page"><button class="nav" data-playlist-action="back">← Mes playlists</button>
      <h1>${esc(playlistPage.title)}</h1><p class="meta">${items.length} vidéos · ${items.filter(item => item.available).length} disponibles hors ligne${playlistPage.filtered ? ' · ' + playlistPage.filtered + ' courtes ou indisponibles masquées' : ''}</p>
      <p class="playlist-description">${esc(playlistPage.description)}</p>${message}<div class="filters">
      <button class="search" data-playlist-action="resume" ${items.length ? '' : 'disabled'}>▶ Reprendre la playlist</button>
      <button class="search" data-playlist-action="download" id="playlistDownload" ${playlistSelection.size > 0 && playlistSelection.size <= 20 ? '' : 'disabled'}>Télécharger la sélection${playlistSelection.size ? ' (' + playlistSelection.size + '/20)' : ''}</button>
      <button class="nav" data-playlist-action="refresh">Actualiser</button><button class="nav" data-playlist-action="remove">Retirer de Fauteuil</button></div>
      <p class="meta">Sélectionne jusqu’à 20 vidéos pour les conserver hors ligne. Retirer la playlist ne supprime aucun fichier.</p>
      <div class="playlist-episodes">${items.map(item => `<div class="playlist-episode">
      <label><input type="checkbox" data-playlist-video="${esc(item.id)}" ${item.kept ? 'disabled' : ''} ${playlistSelection.has(item.id) ? 'checked' : ''} aria-label="Télécharger ${esc(item.title)}"></label>
      <span class="meta">${item.position + 1}</span><a href="${esc(playlistDetailLink(item))}"><img loading="lazy" src="${esc(item.thumbnail)}" alt=""><span><strong>${esc(item.title)}</strong><small>${Math.ceil(item.duration / 60)} min · ${item.available ? 'Disponible hors ligne' : 'YouTube'}${item.finished ? ' · Vu' : item.progress?.current > 5 ? ' · En cours' : ''}</small></span></a></div>`).join('')}</div></section>`;
  }
  return `<h1>Playlists YouTube</h1><div class="filters"><button class="search" data-playlist-action="sync">Actualiser mes playlists Google</button></div>
    <form id="playlistImportForm" class="playlist-import"><input id="playlistUrl" aria-label="Lien d’une playlist YouTube" placeholder="Colle le lien d’une playlist YouTube" required><button class="search">Ajouter la playlist</button></form>${message}
    <div class="episode-grid">${playlistList.map(item => `<button class="card playlist-card" data-playlist="${esc(item.id)}"><img loading="lazy" src="${esc(item.thumbnail)}" alt=""><div class="info"><strong>${esc(item.title)}</strong><div class="meta">${item.total} vidéos · ${esc(item.channel)}</div></div></button>`).join('')}</div>
    ${playlistList.length ? '' : '<p class="status">Retrouve les playlists créées sur ton compte Google ou ajoute une playlist avec son lien. Les vidéos se téléchargent uniquement à ta demande.</p>'}`;
}

async function playlistApi(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok || result.error) throw Error(result.error || 'Playlists indisponibles.');
  return result;
}

async function loadPlaylists({ refresh = false, sync = false } = {}) {
  const opening = !playlistPage;
  const request = ++playlistRequest;
  playlistNotice = selectedPlaylist ? 'Chargement de la playlist…' : 'Chargement des playlists…';
  render(allItems);
  try {
    if (sync) { playlistSyncAttempted = true; await playlistApi('/api/playlists/sync', {}); }
    if (selectedPlaylist) {
      const page = await playlistApi('/api/playlist?id=' + encodeURIComponent(selectedPlaylist) + (refresh ? '&refresh=1' : ''));
      if (request !== playlistRequest || mode !== 'playlists') return;
      playlistPage = page;
      playlistNotice = page.notice || '';
    } else {
      let result = await playlistApi('/api/playlists');
      if (!result.items?.length && !playlistSyncAttempted) {
        playlistSyncAttempted = true;
        await playlistApi('/api/playlists/sync', {});
        result = await playlistApi('/api/playlists');
      }
      if (request !== playlistRequest || mode !== 'playlists') return;
      playlistList = result.items || [];
      playlistNotice = '';
    }
  } catch (error) {
    if (request === playlistRequest) playlistNotice = error.message;
  }
  if (mode === 'playlists' && request === playlistRequest) { render(allItems); if (opening) window.scrollTo(0, 0); }
}

async function importPlaylist(value) {
  mode = 'playlists';
  selectedPlaylist = '';
  playlistPage = null;
  playlistNotice = 'Ajout de la playlist…';
  rememberView(); render(allItems);
  const request = ++playlistRequest;
  try {
    const page = await playlistApi('/api/playlist/import', { url: value });
    if (request !== playlistRequest || mode !== 'playlists') return;
    selectedPlaylist = page.id; playlistPage = page; playlistSelection.clear();
    playlistNotice = page.notice || ''; rememberView(); render(allItems);
  } catch (error) {
    if (request === playlistRequest && mode === 'playlists') { playlistNotice = error.message; render(allItems); }
  }
}

function looksLikePlaylistLink(value) {
  try {
    const url = new URL(value);
    return ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'].includes(url.hostname) && /^[A-Za-z0-9_-]{10,120}$/.test(url.searchParams.get('list') || '');
  } catch { return false; }
}

document.addEventListener('submit', event => {
  if (event.target.id !== 'playlistImportForm') return;
  event.preventDefault(); importPlaylist(document.querySelector('#playlistUrl').value.trim());
});
document.addEventListener('change', event => {
  const id = event.target.dataset.playlistVideo;
  if (!id) return;
  if (event.target.checked) playlistSelection.add(id); else playlistSelection.delete(id);
  const button = document.querySelector('#playlistDownload');
  if (button) { button.disabled = playlistSelection.size === 0 || playlistSelection.size > 20; button.textContent = playlistSelection.size ? `Télécharger la sélection (${playlistSelection.size}/20)` : 'Télécharger la sélection'; }
});
document.addEventListener('click', async event => {
  const card = event.target.closest('[data-playlist]');
  if (card) {
    selectedPlaylist = card.dataset.playlist; playlistPage = null; playlistSelection.clear(); rememberView(); loadPlaylists(); return;
  }
  const button = event.target.closest('[data-playlist-action]');
  if (!button) return;
  const action = button.dataset.playlistAction;
  if (action === 'back') { selectedPlaylist = ''; playlistPage = null; playlistSelection.clear(); rememberView(); loadPlaylists(); return; }
  if (action === 'resume') {
    const items = playlistPage?.items || [];
    const item = items.find(item => !item.finished && item.progress?.current > 5) || items.find(item => !item.finished) || items[0];
    if (item) location.href = playlistDetailLink(item);
    return;
  }
  button.disabled = true;
  try {
    if (action === 'refresh') await loadPlaylists({ refresh: true });
    if (action === 'sync') await loadPlaylists({ sync: true });
    if (action === 'remove') {
      await playlistApi('/api/playlist/remove', { id: selectedPlaylist });
      selectedPlaylist = ''; playlistPage = null; playlistSelection.clear(); rememberView(); await loadPlaylists();
    }
    if (action === 'download') {
      await playlistApi('/api/playlist/download', { id: selectedPlaylist, ids: [...playlistSelection] });
      setView('offline');
    }
  } catch (error) { playlistNotice = error.message; render(allItems); }
  finally { button.disabled = false; }
});

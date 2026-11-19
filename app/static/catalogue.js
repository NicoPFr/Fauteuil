const catalogue = document.querySelector('#catalogue');
const status = document.querySelector('#status');
let allItems = [];
let subscriptions = [];
let trending = [];
let mode = 'all';

const htmlEntities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = value => String(value || '').replace(/[&<>"']/g, character => htmlEntities[character]);

function card(item) {
  const { id, progress, thumbnail, title, collection, channel, season, duration, availabilityLabel } = item;
  const percent = progress?.duration ? Math.min(100, 100 * progress.current / progress.duration) : 0;
  return `<a class="card" style="color:inherit;text-decoration:none" href="${esc(detailLink(id))}">
    <img loading="lazy" alt="" src="${esc(thumbnail)}">
    <div class="info"><div class="title">${esc(title)}</div>
      <div class="meta">${esc(collection || channel || 'YouTube')}${season ? ' · ' + esc(season) : ''}
        ${duration ? ' · ' + Math.floor(duration / 60) + ' min' : ''}
        ${availabilityLabel ? '<br>' + esc(availabilityLabel) : ''}</div>
      ${percent ? `<div class="bar"><i style="width:${percent}%"></i></div>` : ''}
    </div>
  </a>`;
}

function rail(name, items) {
  return items.length ? `<section class="rail"><h2>${esc(name)}</h2><div class="row">${items.map(card).join('')}</div></section>` : '';
}

const viewLabels = {library:'Mes vidéos', playlists:'Playlists', subscriptions:'Abonnements YouTube', catalogue:'Catalogue', films:'Films', series:'Séries', list:'Ma liste', offline:'Hors ligne', history:'Historique'};
const browsableViews = new Set(Object.keys(viewLabels));
const seriesLocation = new URLSearchParams(location.hash.slice(1));
const initialView = seriesLocation.get('vue');
if (browsableViews.has(initialView)) mode = initialView;
let selectedSeries = seriesLocation.get('serie');
let selectedSeason = Number(seriesLocation.get('saison')) || 0;
const searchField = document.querySelector('#q');
window.focusCinemaSearch = () => {
  if (document.querySelector('dialog[open], .modal.open') || document.querySelector('#player.open')) return true;
  searchField.focus();
  searchField.select();
  return true;
};
let searchTerm = '';
let searchMessage = '';
let searchItems = [];
let searchRequest = null;
searchField.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.isComposing) search();
});
let searchHistory = [];
let catalogueLoad = 0;
if (selectedSeries) mode = 'series';

function detailLink(id) {
  const params = new URLSearchParams({ id });
  if (location.hash.length > 1) params.set('from', location.hash);
  return '/detail?' + params;
}

function rememberView() {
  const params = new URLSearchParams();
  if (browsableViews.has(mode)) params.set('vue', mode);
  if (mode === 'playlists' && selectedPlaylist) params.set('playlist', selectedPlaylist);
  if (mode === 'series' && selectedSeries) {
    params.set('serie', selectedSeries);
    if (selectedSeason) params.set('saison', selectedSeason);
  }
  history.replaceState(null, '', '/' + (params.size ? '#' + params : ''));
}

function selectSeries(seriesId, season = 0) {
  cancelSearch();
  selectedSeries = seriesId;
  selectedSeason = season;
  mode = 'series';
  tripStatusPanel.hidden = true;
  rememberView();
  render(allItems);
  catalogue.scrollIntoView({ block: 'start' });
}
catalogue.addEventListener('click', event => {
  const button = event.target.closest('button[data-series]');
  if (button) selectSeries(button.dataset.series || null);
});
catalogue.addEventListener('change', event => {
  if (event.target.id === 'seasonSelect') selectSeries(selectedSeries, Number(event.target.value));
});

function seriesRail(groups) {
  if (!groups.size) return '';
  const cards = [...groups].map(([id, episodes]) => {
    const seasons = new Set(episodes.map(episode => episode.season_number));
    const cover = episodes.find(episode => episode.thumbnail)?.thumbnail;
    return `<button class="card series-card" data-series="${esc(id)}">
      ${cover ? `<img loading="lazy" alt="" src="${esc(cover)}">` : ''}
      <div class="info"><div class="title">${esc(episodes[0].collection)}</div>
      <div class="meta">${seasons.size} saison${seasons.size > 1 ? 's' : ''} · ${episodes.length} épisode${episodes.length > 1 ? 's' : ''}</div></div>
    </button>`;
  }).join('');
  return `<section class="rail"><h2>Séries</h2><div class="row">${cards}</div></section>`;
}

function seriesPage(episodes) {
  const seasons = [...new Set(episodes.map(episode => episode.season_number))];
  const season = seasons.includes(selectedSeason) ? selectedSeason : seasons[0];
  const options = seasons.map(number =>
    `<option value="${number}" ${number === season ? 'selected' : ''}>Saison ${number}</option>`
  ).join('');
  return `<section class="rail">
    <button class="nav" data-series="">← Toutes les séries</button>
    <div class="series-toolbar"><h2>${esc(episodes[0].collection)}</h2>
    <select id="seasonSelect" aria-label="Choisir une saison">${options}</select></div>
    <div class="episode-grid">${episodes.filter(episode => episode.season_number === season).map(card).join('')}</div>
  </section>`;
}

function discoveryRails(items) {
  const available = new Map(items.filter(item => !item.local).map(item => [item.id, item]));
  const shown = new Set([...subscriptions, ...trending].map(item => item.id));
  let html = '';
  for (const { term, ids } of searchHistory) {
    const recommendations = ids.map(id => available.get(id))
      .filter(item => item && !shown.has(item.id)).slice(0, 12);
    for (const item of recommendations) shown.add(item.id);
    html += rail('Suite à ta recherche « ' + term + ' »', recommendations);
  }
  return html + rail('À découvrir', [...available.values()].filter(item => !shown.has(item.id)));
}

async function openImports() {
  const panel = document.querySelector('#importsPanel');
  const list = document.querySelector('#importsList');
  const message = document.querySelector('#importsMessage');
  list.replaceChildren();
  message.textContent = 'Vérification des fichiers…';
  if (!panel.open) panel.showModal();
  try {
    const response = await fetch('/api/local/imports');
    const entries = await response.json();
    if (!response.ok || !Array.isArray(entries)) throw Error('Liste des fichiers indisponible.');
    message.textContent = entries.length ? '' : 'Aucun fichier ajouté manuellement.';
    for (const entry of entries) {
      const row = document.createElement('li');
      const description = document.createElement('div');
      const title = document.createElement('strong');
      const path = document.createElement('p');
      title.textContent = entry.title;
      path.textContent = entry.available ? entry.path : 'Fichier indisponible · ' + entry.path;
      description.append(title, path);
      const button = document.createElement('button');
      button.className = 'nav';
      button.type = 'button';
      button.textContent = 'Retirer';
      button.setAttribute('aria-label', 'Retirer la référence de ' + entry.title);
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const response = await fetch('/api/local/unlink', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: entry.path })
          });
          const result = await response.json();
          if (!response.ok || !result.ok) throw Error(result.error || 'Impossible de retirer la référence.');
          row.remove();
          message.textContent = 'Référence retirée.';
          await load();
        } catch (error) {
          message.textContent = error.message;
          button.disabled = false;
        }
      });
      row.append(description, button);
      list.append(row);
    }
  } catch (error) {
    message.textContent = error.message;
  }
}

function chooseLocalVideos() {
  const importer = window.webkit?.messageHandlers?.importLocalVideos;
  if (importer) importer.postMessage(null);
  else status.textContent = 'Ouvre Fauteuil.app pour sélectionner des fichiers.';
}

window.localImportFinished = async function (added, skipped, error) {
  if (error) {
    status.textContent = error;
    return;
  }
  setView('films');
  await load();
  const count = added > 1 ? added + ' vidéos ajoutées.' : added + ' vidéo ajoutée.';
  status.textContent = skipped ? count + ' ' + skipped + ' déjà présentes ou indisponibles.' : count;
};

const tripPicker = document.querySelector('#tripPicker');
const tripMessage = document.querySelector('#tripMessage');
const tripStatusPanel = document.querySelector('#tripStatus');
const cancelTripButton = document.querySelector('#cancelTrip');
let tripPollId = 0;

function tripCandidates() {
  const localIds = new Set(allItems.filter(item => item.local).map(item => item.source_id));
  return allItems.filter(item => item.watchlisted && !item.local && !item.kept && !localIds.has(item.id));
}

function openTripPicker() {
  const options = document.querySelector('#tripOptions');
  const videos = tripCandidates();
  options.replaceChildren();
  tripMessage.textContent = videos.length ? '' : 'Toutes les vidéos de Ma liste sont déjà disponibles hors ligne.';
  document.querySelector('#tripSubmit').disabled = !videos.length;
  for (const video of videos) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.name = 'video';
    input.value = video.id;
    const title = document.createElement('span');
    title.textContent = video.title;
    if (video.duration > 0) {
      const duration = document.createElement('small');
      duration.textContent = ' · ' + Math.ceil(video.duration / 60) + ' min';
      title.append(duration);
    }
    label.append(input, title);
    options.append(label);
  }
  tripPicker.showModal();
}

document.querySelector('#tripForm').addEventListener('submit', async event => {
  event.preventDefault();
  const ids = new FormData(event.currentTarget).getAll('video');
  if (ids.length < 1 || ids.length > 20) {
    tripMessage.textContent = 'Sélectionne entre une et vingt vidéos.';
    return;
  }
  const button = document.querySelector('#tripSubmit');
  button.disabled = true;
  try {
    const response = await fetch('/api/download/trip', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids })
    });
    const result = await response.json();
    if (!result.ok) throw Error(result.error || 'Impossible de télécharger la sélection.');
    tripPicker.close();
    setView('offline');
  } catch (error) {
    tripMessage.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

cancelTripButton.addEventListener('click', async () => {
  cancelTripButton.disabled = true;
  try {
    const response = await fetch('/api/download/trip/cancel', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.error || 'L’annulation a échoué.');
    cancelTripButton.textContent = 'Arrêt en cours…';
  } catch (error) {
    status.textContent = error.message;
    cancelTripButton.disabled = false;
  }
});

function tripJobLabel(entry) {
  if (entry.state === 'cancelled') return 'Annulée';
  if (entry.state === 'pending') return 'En attente';
  if (entry.state === 'ready') return 'Prête hors ligne';
  if (entry.state === 'failed') return entry.error || 'Téléchargement échoué';
  if (entry.progress?.phase === 'processing') return 'Finalisation';
  if (Number.isFinite(entry.progress?.percent)) return 'Téléchargement · ' + Math.floor(entry.progress.percent) + ' %';
  return 'Préparation';
}

async function showTripStatus() {
  const poll = ++tripPollId;
  let downloading = false;
  while (mode === 'offline' && poll === tripPollId) {
    try {
      const response = await fetch('/api/download/trip');
      if (!response.ok) throw Error('Suivi des téléchargements indisponible.');
      const result = await response.json();
      if (mode !== 'offline' || poll !== tripPollId) return;
      tripStatusPanel.hidden = !result.items.length;
      cancelTripButton.hidden = !result.active;
      cancelTripButton.disabled = Boolean(result.cancelling);
      cancelTripButton.textContent = result.cancelling ? 'Arrêt en cours…' : 'Annuler les téléchargements';
      const list = document.querySelector('#tripJobs');
      const rows = result.items.map(entry => {
        const row = document.createElement('li');
        const state = document.createElement('small');
        state.textContent = tripJobLabel(entry);
        const title = document.createElement(entry.state === 'ready' ? 'a' : 'span');
        title.textContent = entry.title;
        if (entry.state === 'ready') title.href = detailLink(entry.id);
        row.append(title, state);
        if (Number.isFinite(entry.progress?.percent)) {
          const bar = document.createElement('progress');
          bar.max = 100;
          bar.value = entry.progress.percent;
          bar.setAttribute('aria-label', 'Téléchargement de ' + entry.title);
          row.append(bar);
        }
        return row;
      });
      list.replaceChildren(...rows);
      if (!result.active) {
        if (downloading || result.items.some(entry => entry.state === 'ready')) await load({ refreshYouTube: false });
        return;
      }
      downloading = true;
      await new Promise(resolve => setTimeout(resolve, 1500));
    } catch (error) {
      if (mode === 'offline' && poll === tripPollId) status.textContent = error.message;
      return;
    }
  }
}

function formatStorage(bytes) {
  const units = ['o','Kio','Mio','Gio','Tio'];
  let index = 0;
  while (bytes >= 1024 && index < units.length - 1) {
    bytes /= 1024;
    index++;
  }
  return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: index ? 1 : 0 }).format(bytes) + ' ' + units[index];
}

async function openStorage({ refresh = false } = {}) {
  const panel = document.querySelector('#storagePanel');
  const message = document.querySelector('#storageStatus');
  const list = document.querySelector('#storageMetrics');
  const clean = document.querySelector('#cleanCovers');
  if (!refresh) document.querySelector('#storageCleanupMessage').textContent = '';
  clean.hidden = true;
  list.replaceChildren();
  message.textContent = 'Calcul de l’espace utilisé…';
  if (!panel.open) panel.showModal();
  try {
    const response = await fetch('/api/storage');
    const storage = await response.json();
    if (!response.ok || storage.error) throw Error(storage.error || 'Stockage indisponible.');
    message.textContent = formatStorage(storage.available_bytes) + ' disponibles sur ' + formatStorage(storage.volume_bytes);
    if (storage.unused_covers?.count) {
      clean.hidden = false;
      clean.textContent = 'Nettoyer les jaquettes inutilisées · ' + formatStorage(storage.unused_covers.bytes);
    }
    const metrics = [
      ['Vidéos personnelles référencées', 'personal_video_bytes'],
      ['Téléchargements temporaires', 'temporary_bytes'],
      ['Vidéos conservées hors ligne', 'retained_video_bytes'],
      ['Jaquettes en cache', 'cover_bytes'],
      ['Autres caches', 'other_cache_bytes'],
      ['Base et données de Fauteuil', 'database_bytes']
    ];
    for (const [label, key] of metrics) {
      const title = document.createElement('dt');
      const value = document.createElement('dd');
      title.textContent = label;
      value.textContent = formatStorage(storage[key]);
      list.append(title, value);
    }
  } catch (error) {
    message.textContent = error.message;
  }
}

async function cleanUnusedCovers() {
  const button = document.querySelector('#cleanCovers');
  const message = document.querySelector('#storageCleanupMessage');
  button.disabled = true;
  try {
    const response = await fetch('/api/storage/clean-covers', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.error || 'Nettoyage indisponible.');
    message.textContent = result.count
      ? formatStorage(result.bytes) + ' libérés dans le cache de jaquettes.'
      : 'Aucune jaquette inutilisée.';
    await openStorage({ refresh: true });
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function searchWords(value) {
  return String(value || '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
    .match(/[\p{L}\p{N}]+/gu) || [];
}

function matchesSearch(term, ...fields) {
  const text = searchWords(fields.join(' ')).join(' ');
  const words = searchWords(term);
  return words.length > 0 && words.every(word => text.includes(word));
}

function youtubeIdInQuery(term) {
  try {
    const url = new URL(term);
    if (!['https:', 'http:'].includes(url.protocol)) return '';
    const video = url.hostname === 'youtu.be' ? url.pathname.replace(/^\/|\/$/g,'')
      : ['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(url.hostname) &&
        url.pathname.replace(/\/$/,'') === '/watch' ? url.searchParams.get('v') : '';
    return /^[A-Za-z0-9_-]{11}$/.test(video || '') ? video : '';
  } catch { return ''; }
}

function renderSearch(items, groups) {
  const local = items.filter(item => item.local);
  const linkedId = youtubeIdInQuery(searchTerm);
  const series = new Map([...groups].filter(([, episodes]) => matchesSearch(searchTerm, episodes[0].collection)));
  const localResults = local.filter(item =>
    !series.has(item.series_id) && (linkedId ? item.source_id === linkedId && item.duration > 180 && !/#shorts|\bshorts\b/i.test(item.title)
      : matchesSearch(searchTerm, item.title, item.collection, item.channel))
  );
  const localIds = new Set(local.map(item => item.source_id).filter(Boolean));
  const online = searchItems.filter(item => !item.local && !localIds.has(item.id));
  for (const item of searchItems) {
    if (item.local && !localResults.some(row => row.id === item.id)) localResults.push(item);
  }
  // A remote title can identify a local copy even if its filename differs.
  const matchingCopies = new Set(searchItems.filter(item => localIds.has(item.id)).map(item => item.id));
  const existing = new Set(localResults.map(item => item.id));
  for (const item of local) {
    if (matchingCopies.has(item.source_id) && !existing.has(item.id) && !series.has(item.series_id)) {
      localResults.push(item);
    }
  }
  const html = rail('Dans ta bibliothèque', localResults) + seriesRail(series) + rail('Sur YouTube', online);
  const message = searchMessage ? `<p class="status" role="status">${esc(searchMessage)}</p>` : '';
  return message + (html || (searchRequest ? '' : '<p>Aucune vidéo trouvée.</p>'));
}

function contentKey(item) {
  return item.source_id || item.id;
}

function nextSuggestions(items, groups) {
  const history = items.filter(item => item.finished_at)
    .sort((a, b) => b.finished_at - a.finished_at).slice(0, 12);
  const excluded = new Set(items.filter(item => item.finished_at || item.progress?.current > 5).map(contentKey));
  const selectedSeries = new Set();
  const chosen = [];
  const add = item => {
    if (excluded.has(contentKey(item))) return;
    chosen.push(item);
    excluded.add(contentKey(item));
  };
  for (const watched of history) {
    if (!watched.series_id || selectedSeries.has(watched.series_id)) continue;
    selectedSeries.add(watched.series_id);
    const episodes = groups.get(watched.series_id) || [];
    const position = episodes.findIndex(item => item.id === watched.id);
    const next = episodes.slice(position + 1)
      .find(item => !excluded.has(contentKey(item)));
    if (next) add(next);
  }
  const channels = new Set(history.map(item => item.channel));
  channels.delete('YouTube');
  channels.delete('Sur cet appareil');
  channels.delete(undefined);
  channels.delete(null);
  for (const candidate of items) {
    if (chosen.length >= 12) break;
    if (!candidate.series_id && channels.has(candidate.channel)) add(candidate);
  }
  return chosen.slice(0, 12);
}

function recentlyAdded(items) {
  const candidates = items.filter(item => (item.local || item.kept) && item.added_at)
    .sort((a, b) => b.added_at - a.added_at);
  const shown = new Set();
  const latest = [];
  for (const item of candidates) {
    const key = item.series_id ? 'series:' + item.series_id : contentKey(item);
    if (shown.has(key)) continue;
    shown.add(key);
    latest.push(item);
    if (latest.length === 12) break;
  }
  return latest;
}

const homeHero = document.querySelector('#homeHero');
const featuredCover = document.querySelector('#featuredCover');
const featuredLink = document.querySelector('#featuredLink');
featuredCover.addEventListener('load', () => { featuredCover.hidden = false; });
featuredCover.addEventListener('error', () => { featuredCover.hidden = true; });

function chooseFeatured(items) {
  const resume = [...items].filter(item => item.progress?.current > 5)
    .sort((a, b) => b.progress.updated - a.progress.updated);
  if (resume.length) return { item: resume[0], context: 'Reprendre la lecture' };
  const recent = recentlyAdded(items);
  const feature = recent.find(item => !item.series_id && item.thumbnail)
    || recent.find(item => item.thumbnail) || recent[0];
  if (feature) return { item: feature, context: 'Ajout récent' };
  const available = items.find(item => item.thumbnail) || items[0];
  return available ? { item: available, context: 'À découvrir' } : null;
}

function renderFeatured(items) {
  homeHero.hidden = mode !== 'all';
  if (homeHero.hidden) return;
  const featured = chooseFeatured(items);
  const item = featured?.item;
  const title = item?.series_id ? item.collection + ' · ' + item.title : item?.title;
  document.querySelector('#featuredTitle').textContent = title || 'Fauteuil';
  document.querySelector('#featuredContext').textContent = featured?.context || '';
  const duration = item?.duration ? Math.floor(item.duration / 60) + ' min' : '';
  const available = item?.local || item?.downloaded || item?.kept ? 'Disponible hors ligne' : 'À télécharger';
  document.querySelector('#featuredMeta').textContent = item
    ? [item.genre || '', duration, available].filter(Boolean).join(' · ')
    : 'Retrouve tes films, séries et vidéos hors ligne.';
  featuredLink.href = item ? detailLink(item.id) : '#catalogue';
  featuredLink.textContent = item ? 'Voir la fiche' : 'Explorer la bibliothèque';
  const cover = item?.thumbnail || '';
  if ((featuredCover.getAttribute('src') || '') !== cover) {
    featuredCover.hidden = true;
    if (cover) featuredCover.src = cover;
    else featuredCover.removeAttribute('src');
  }
}

const catalogueFilters = document.querySelector('#catalogueFilters');
const catalogueGenre = document.querySelector('#catalogueGenre');
catalogueFilters.addEventListener('change', () => render(allItems));
catalogueFilters.addEventListener('submit', event => event.preventDefault());

function unifiedCatalogue(items) {
  const unique = new Map();
  for (const item of items) {
    const key = contentKey(item);
    const existing = unique.get(key);
    if (!existing) unique.set(key, item);
    else {
      const prefer = item.local && !existing.local || item.kept && !existing.local && !existing.kept;
      const selected = prefer ? item : existing;
      unique.set(key, { ...selected, genre: selected.genre || (prefer ? existing.genre : item.genre) });
    }
  }
  return [...unique.values()];
}

function catalogueMatches(item, { genre, availability, duration }) {
  const offline = item.local || item.downloaded;
  if (genre && item.genre !== genre) return false;
  if (availability === 'offline' && !offline || availability === 'online' && offline) return false;
  if (!duration) return true;
  if (!item.duration) return false;
  if (duration === 'short') return item.duration < 20 * 60;
  if (duration === 'medium') return item.duration >= 20 * 60 && item.duration < 60 * 60;
  return item.duration >= 60 * 60;
}

function renderFilteredCatalogue(items) {
  const unified = unifiedCatalogue(items);
  const genres = [...new Set(unified.map(item => item.genre).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, 'fr'));
  const current = catalogueGenre.value;
  if (genres.join('\n') !== catalogueGenre.dataset.genres) {
    catalogueGenre.replaceChildren(new Option('Tous', ''), ...genres.map(genre => new Option(genre, genre)));
    catalogueGenre.value = genres.includes(current) ? current : '';
    catalogueGenre.dataset.genres = genres.join('\n');
  }
  const filters = {
    genre: catalogueGenre.value, availability: document.querySelector('#catalogueAvailability').value,
    duration: document.querySelector('#catalogueDuration').value
  };
  const results = unified.filter(item => catalogueMatches(item, filters));
  if (!results.length) return '<p>Aucun titre ne correspond à ces filtres.</p>';
  return `<p class="status" role="status">${results.length} titre${results.length > 1 ? 's' : ''}</p>
    <div class="episode-grid">${results.map(item => card({
      ...item, availabilityLabel: item.local || item.downloaded ? 'Disponible hors ligne' : 'À télécharger'
    })).join('')}</div>`;
}

const personalLibrary = document.querySelector('#personalLibrary');
const personalSearch = document.querySelector('#personalSearch');
personalSearch.addEventListener('input', () => {
  catalogue.innerHTML = renderPersonalLibrary(allItems);
});
const titleOrder = new Intl.Collator('fr', { numeric: true, sensitivity: 'base' });

function renderPersonalLibrary(items) {
  const term = personalSearch.value.trim();
  const candidates = unifiedCatalogue(items.filter(item => item.local || item.downloaded));
  if (!candidates.length) return '<p role="status">Aucune vidéo disponible sur ce Mac.</p>';
  const results = candidates.filter(item =>
    !term || matchesSearch(term, item.title, item.collection, item.channel)
  ).sort((a, b) => titleOrder.compare(a.collection || a.title, b.collection || b.title) ||
    titleOrder.compare(a.season || '', b.season || '') || titleOrder.compare(a.title, b.title));
  if (!results.length) return '<p role="status">Aucune de tes vidéos ne correspond à cette recherche.</p>';
  return `<p class="status">${results.length} vidéo${results.length > 1 ? 's' : ''} sur le Mac</p>
    <div class="episode-grid">${results.map(item => card({
      ...item,
      availabilityLabel: item.local ? 'Fichier personnel' : item.kept ? 'Conservée hors ligne' : 'Téléchargement temporaire'
    })).join('')}</div>`;
}

function renderHistory(items) {
  const entries = new Map();
  for (const item of items) {
    const progress = item.progress?.current > 0 ? item.progress.updated : 0;
    const finished = item.finished_at || 0;
    const watchedAt = Math.max(progress, finished);
    if (!watchedAt) continue;
    const key = contentKey(item);
    if ((entries.get(key)?.watchedAt || 0) >= watchedAt) continue;
    entries.set(key, {
      ...item,
      watchedAt,
      availabilityLabel: progress > finished ? 'En cours' : 'Terminée'
    });
  }
  const ordered = [...entries.values()].sort((a, b) => b.watchedAt - a.watchedAt);
  return ordered.length
    ? '<div class="episode-grid">' + ordered.map(card).join('') + '</div>'
    : '<p>Les vidéos commencées ou terminées apparaîtront ici.</p>';
}

function render(items) {
  renderFeatured(items);
  const personal = mode === 'library';
  personalLibrary.hidden = !personal;
  document.querySelector('#catalogueNavigation').hidden = personal || mode === 'playlists';
  document.querySelector('#browseTopics').hidden = personal || mode === 'playlists';
  status.hidden = personal || mode === 'playlists';
  document.querySelectorAll('button[data-view]').forEach(button => {
    const selected = button.dataset.view === mode;
    button.classList.toggle(button.classList.contains('nav') ? 'active' : 'on', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  catalogueFilters.hidden = mode !== 'catalogue';
  const films = items.filter(item => item.local && !item.series_id);
  const groups = new Map();
  for (const item of items) {
    if (!item.series_id) continue;
    if (!groups.has(item.series_id)) groups.set(item.series_id, []);
    groups.get(item.series_id).push(item);
  }
  let html = '';
  if (mode === 'playlists') {
    html = renderPlaylists();
  } else if (mode === 'library') {
    html = renderPersonalLibrary(items);
  } else if (mode === 'series') {
    html = groups.has(selectedSeries) ? seriesPage(groups.get(selectedSeries)) : seriesRail(groups);
  } else if (mode === 'films') {
    html = rail('Films', films);
  } else if (mode === 'search') {
    html = renderSearch(items, groups);
  } else if (mode === 'catalogue') {
    html = renderFilteredCatalogue(items);
  } else if (mode === 'offline') {
    html = rail('Téléchargements conservés', items.filter(item => item.kept)) +
      rail('Téléchargements temporaires', items.filter(item => item.downloaded && !item.kept)) +
      rail('Films sur le Mac', films) + seriesRail(groups);
  } else if (mode === 'history') {
    html = renderHistory(items);
  } else if (mode === 'subscriptions') {
    html = subscriptions.length
      ? '<div class="episode-grid">' + subscriptions.map(card).join('') + '</div>'
      : '<p role="status">' + esc(subscriptionsMessage) + '</p>';
  } else if (mode === 'list') {
    const saved = items.filter(item => item.watchlisted);
    html = (tripCandidates().length
      ? '<button class="search" type="button" onclick="openTripPicker()">Télécharger des vidéos</button>'
      : '') + rail('Ma liste', saved);
  } else {
    const resume = items.filter(item => item.progress?.current > 5);
    resume.sort((a,b) => b.progress.updated - a.progress.updated);
    html = rail('Reprendre la lecture', resume) +
      rail('Nouveautés de tes abonnements', subscriptions) +
      rail('À regarder ensuite', nextSuggestions(items, groups)) +
      rail('Ajouts récents', recentlyAdded(items)) +
      discoveryRails(items) +
      rail('Tendances en France', trending) +
      rail('Films', films) + seriesRail(groups) +
      rail('Ma liste', items.filter(item => item.watchlisted));
  }
  catalogue.innerHTML = html || (mode === 'search' ? '' : '<p>Aucune vidéo dans cette rubrique.</p>');
}
async function openProfiles() {
  document.querySelector('#profiles').classList.add('open');
  const response = await fetch('/api/profiles');
  const { profiles, active } = await response.json();
  const current = profiles.find(profile => profile.id === active);
  document.querySelector('#profileButton').textContent = (current?.avatar || '🍿') + ' ' + (current?.name || 'Principal');
  document.querySelector('#profileList').innerHTML = profiles.map(profile =>
    `<div><button onclick="selectProfile('${profile.id}')">${esc(profile.avatar)} ${esc(profile.name)}${profile.id === active ? ' ✓' : ''}</button>` +
    (profile.id !== 'default' ? `<button onclick="deleteProfile('${profile.id}')">Supprimer</button>` : '') +
    '</div>'
  ).join('');
}

function closeProfiles() {
  document.querySelector('#profiles').classList.remove('open');
}

async function selectProfile(id) {
  await fetch('/api/profiles/select', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id })
  });
  closeProfiles();
  home();
}

async function createProfile() {
  const field = document.querySelector('#newProfile');
  const name = field.value.trim();
  if (!name) return;
  const response = await fetch('/api/profiles', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, avatar: '🎬' })
  });
  if ((await response.json()).ok) {
    field.value = '';
    closeProfiles();
    home();
  }
}

async function deleteProfile(id) {
  if (!confirm('Supprimer ce profil, sa liste et sa progression ?')) return;
  await fetch('/api/profiles/delete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id })
  });
  closeProfiles();
  home();
}

const googlePanel = document.querySelector('#settings');
const googleMessage = document.querySelector('#googleStatus');
const googleFile = document.querySelector('#googleFile');
const googleImport = document.querySelector('#googleImport');
const googleConnect = document.querySelector('#googleConnect');
const googleDisconnect = document.querySelector('#googleDisconnect');
let googlePoll = 0;
let subscriptionLoad = 0;
let subscriptionsMessage = 'Chargement des abonnements…';

async function googleRequest(action, data) {
  const options = data === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
  };
  const response = await fetch('/api/google/' + action, options);
  const result = await response.json();
  if (!response.ok || result.error) throw Error(result.error || 'La connexion Google est indisponible.');
  return result;
}

function renderGoogleState(state) {
  document.querySelector('#googleSetup').hidden = state.configured || state.connected;
  googleConnect.hidden = !state.configured || state.connected;
  googleDisconnect.hidden = !state.connected;
  googleMessage.textContent = state.connected ? 'Compte Google connecté.' : state.message || '';
}

function closeSettings() {
  googlePanel.classList.remove('open');
}

async function openGoogleSetup(step) {
  try {
    await googleRequest('setup', { step });
  } catch (error) {
    googleMessage.textContent = error.message;
  }
}

googleImport.addEventListener('click', () => googleFile.click());
googleFile.addEventListener('change', async () => {
  const file = googleFile.files[0];
  googleFile.value = '';
  if (!file) return;
  googleImport.disabled = true;
  try {
    if (file.size > 64 * 1024) throw Error('Sélectionne le fichier JSON téléchargé depuis Google Cloud.');
    let credentials;
    try {
      credentials = JSON.parse((await file.text()).trim())?.installed;
    } catch {
      throw Error('Sélectionne le fichier JSON téléchargé depuis Google Cloud.');
    }
    if (!credentials?.client_id || !credentials?.client_secret) {
      throw Error('Choisis le fichier JSON d’un client Google de type « Application de bureau ».');
    }
    await googleRequest('settings', { client_id: credentials.client_id, client_secret: credentials.client_secret });
    renderGoogleState({ configured: true, connected: false });
    await connectGoogle();
  } catch (error) {
    googleMessage.textContent = error.message;
  } finally {
    googleImport.disabled = false;
  }
});

async function connectGoogle() {
  if (googleConnect.disabled) return;
  googleConnect.disabled = true;
  try {
    await beginGoogleConnect(++googlePoll);
  } finally {
    googleConnect.disabled = false;
  }
}

async function googleButtonAction() {
  googlePanel.classList.add('open');
  try {
    const state = await googleRequest('status?check=1');
    renderGoogleState(state);
    if (state.configured && !state.connected) await connectGoogle();
  } catch (error) {
    googleMessage.textContent = error.message;
  }
}

async function beginGoogleConnect(poll) {
  try {
    await googleRequest('start', {});
    googleMessage.textContent = 'Autorise Fauteuil dans le navigateur.';
    for (let attempt = 0; attempt < 135 && poll === googlePoll; attempt++) {
      const state = await googleRequest('status');
      if (poll !== googlePoll) return;
      if (state.status === 'error') throw Error(state.message);
      if (state.connected) {
        renderGoogleState(state);
        document.querySelector('#account').textContent = 'Compte Google ✓';
        loadSubscriptions();
        return;
      }
      if (state.status === 'exchanging') googleMessage.textContent = 'Validation de la connexion Google…';
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    if (poll === googlePoll) googleMessage.textContent = 'La connexion Google a expiré. Réessaie.';
  } catch (error) {
    if (poll === googlePoll) googleMessage.textContent = error.message;
  }
}

async function disconnectGoogle() {
  googlePoll++;
  subscriptionLoad++;
  try {
    await googleRequest('disconnect', {});
    document.querySelector('#account').textContent = 'Connecter Google';
    renderGoogleState(await googleRequest('status'));
    googleMessage.textContent = 'Compte déconnecté.';
    subscriptions = [];
    subscriptionsMessage = 'Connecte ton compte Google pour retrouver tes abonnements.';
    trending = [];
    render(allItems);
  } catch (error) {
    googleMessage.textContent = error.message;
  }
}

function home() {
  tripPollId++;
  tripStatusPanel.hidden = true;
  cancelSearch();
  selectedSeries = null;
  selectedSeason = 0;
  mode = 'all';
  rememberView();
  status.textContent = 'Accueil cinéma';
  load();
}

function setView(view) {
  tripPollId++;
  tripStatusPanel.hidden = true;
  cancelSearch();
  selectedSeries = null;
  selectedSeason = 0;
  mode = view;
  rememberView();
  render(allItems);
  status.textContent = viewLabels[view] || 'Catalogue';
  if (view === 'library') window.scrollTo(0, 0);
  if (view === 'offline') showTripStatus();
  if (view === 'subscriptions') loadSubscriptions();
  if (view === 'playlists') loadPlaylists();
}

async function load({ refreshYouTube = true } = {}) {
  const request = ++catalogueLoad;
  try {
    const [profiles, items, history] = await Promise.all(
      ['/api/profiles', '/api/library', '/api/search-history'].map(async url => {
        const response = await fetch(url);
        if (!response.ok) throw Error(response.statusText);
        return response.json();
      })
    );
    if (request !== catalogueLoad) return;
    const active = profiles.profiles.find(profile => profile.id === profiles.active);
    document.querySelector('#profileButton').textContent = (active?.avatar || '🍿') + ' ' + (active?.name || 'Principal');
    if (browsableViews.has(mode) && status.textContent === 'Chargement de votre catalogue…') status.textContent = viewLabels[mode];
    allItems = items;
    searchHistory = history;
    render(allItems);
    if (refreshYouTube) loadSubscriptions();
  } catch (error) {
    if (request === catalogueLoad) status.textContent = 'Impossible de charger la bibliothèque. ' + error.message;
  }
}
window.refreshYouTubeOnReturn = () => {
  if (mode === 'all' || mode === 'subscriptions') loadSubscriptions();
};

async function loadSubscriptions() {
  const load = ++subscriptionLoad;
  const refresh = async (name, onResult) => {
    try {
      const result = await googleRequest(name);
      if (load !== subscriptionLoad) return;
      onResult(result);
      if (mode === 'all' || mode === 'subscriptions') render(allItems);
    } catch (error) {
      if (load !== subscriptionLoad || name !== 'suggestions') return;
      subscriptionsMessage = error.message;
      if (mode === 'all' || mode === 'subscriptions') {
        status.textContent = error.message;
        render(allItems);
      }
    }
  };
  await Promise.all([
    refresh('trending', result => { trending = result.items || []; }),
    refresh('suggestions', result => {
      subscriptions = result.items || [];
      subscriptionsMessage = result.connected
        ? 'Aucune nouveauté de tes abonnements pour le moment.'
        : result.notice || 'Connecte ton compte Google pour retrouver tes abonnements.';
      document.querySelector('#account').textContent = result.connected ? 'Compte Google ✓' : 'Connecter Google';
      if (mode === 'all' || mode === 'subscriptions') {
        status.textContent = result.notice || (result.connected
          ? mode === 'subscriptions' ? 'Nouveautés de tes abonnements YouTube' : ''
          : subscriptionsMessage);
      }
    })
  ]);
}

function category(term) {
  searchField.value = term;
  search();
}

function cancelSearch() {
  searchRequest?.abort();
  searchRequest = null;
}

async function search() {
  const term = searchField.value.trim();
  if (!term) {
    status.textContent = 'Saisis un titre ou un sujet.';
    searchField.focus();
    return;
  }
  if (looksLikePlaylistLink(term)) { cancelSearch(); importPlaylist(term); return; }
  cancelSearch();
  const request = new AbortController();
  searchRequest = request;
  searchTerm = term;
  searchItems = [];
  mode = 'search';
  tripStatusPanel.hidden = true;
  history.replaceState(null, '', '/');
  status.textContent = 'Recherche';
  searchMessage = 'Recherche sur YouTube…';
  render(allItems);
  catalogue.scrollIntoView({ block: 'start' });
  try {
    const response = await fetch('/api/search?q=' + encodeURIComponent(term), { signal: request.signal });
    const results = await response.json();
    if (searchRequest !== request) return;
    if (!response.ok || !Array.isArray(results)) {
      searchMessage = results.error || 'La recherche YouTube est indisponible.';
      return;
    }
    searchItems = results;
    searchMessage = youtubeIdInQuery(term) && results.length ? 'Vidéo correspondant au lien' : results.length ? 'Résultats YouTube de plus de 3 min' : 'Aucun résultat YouTube de plus de 3 min.';
  } catch (error) {
    if (searchRequest !== request) return;
    searchMessage = 'La recherche YouTube est indisponible. Les résultats de ta bibliothèque restent visibles.';
  } finally {
    if (searchRequest === request) {
      searchRequest = null;
      render(allItems);
    }
  }
}

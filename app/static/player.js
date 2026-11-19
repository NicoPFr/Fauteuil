let activePath = '';
let activeId = '';
const movie = document.querySelector('#movie');
const player = document.querySelector('#player');
const playerError = document.querySelector('#playerError');
const fullScreenHandler = window.webkit?.messageHandlers?.playerFullScreen;
let windowFullScreen = false;
let enteringFullScreen = false;
let playerControlsTimer;
let lastPlayerPointer = null;

function playerIsFullScreen() {
  return windowFullScreen || movie.webkitDisplayingFullscreen ||
    document.fullscreenElement === player || document.fullscreenElement === movie;
}

function revealPlayerControls() {
  clearTimeout(playerControlsTimer);
  player.classList.remove('controls-idle');
  if (player.classList.contains('open') && !movie.paused && !movie.ended) {
    playerControlsTimer = setTimeout(() => player.classList.add('controls-idle'), 2000);
  }
}

window.cinemaFullScreenChanged = (enabled, failed) => {
  windowFullScreen = enabled;
  enteringFullScreen = false;
  player.classList.toggle('window-fullscreen', enabled);
  revealPlayerControls();
  if (failed && player.classList.contains('open')) showPlayerError('Impossible de changer le mode plein écran.');
};
fullScreenHandler?.postMessage('status');

async function togglePlayerFullscreen() {
  if (playerIsFullScreen()) {
    await leavePlayerFullscreen();
    return;
  }
  // Use the same native video presentation as WebKit's own fullscreen button.
  // In particular, do not resize the app window around an independently composited video.
  if (movie.webkitSupportsFullscreen && typeof movie.webkitEnterFullscreen === 'function') {
    try {
      movie.webkitEnterFullscreen();
      return;
    } catch {} // Older WebKit versions may reject this presentation; retain the window fallback.
  }
  if (fullScreenHandler) {
    if (enteringFullScreen) return;
    enteringFullScreen = !windowFullScreen;
    fullScreenHandler.postMessage(!windowFullScreen);
    return;
  }
  try {
    await player.requestFullscreen();
  } catch {
    showPlayerError('Impossible de passer en plein écran.');
  }
}

async function leavePlayerFullscreen() {
  if (movie.webkitDisplayingFullscreen) {
    try { movie.webkitExitFullscreen?.(); } catch {}
  }
  if (windowFullScreen || enteringFullScreen) fullScreenHandler?.postMessage(false);
  if (document.fullscreenElement === player || document.fullscreenElement === movie) {
    await document.exitFullscreen().catch(() => {});
  }
}

document.addEventListener('fullscreenchange', revealPlayerControls);
player.addEventListener('pointermove', event => {
  const { clientX, clientY } = event;
  if (lastPlayerPointer?.x === clientX && lastPlayerPointer.y === clientY) return;
  lastPlayerPointer = { x: clientX, y: clientY };
  revealPlayerControls();
}, { capture: true });
player.addEventListener('pointerdown', revealPlayerControls);
player.addEventListener('focusin', revealPlayerControls);
movie.addEventListener('pause', revealPlayerControls);
movie.addEventListener('ended', revealPlayerControls);
let lastReport = 0;
let progressRequest = Promise.resolve();
let playerGeneration = 0;
const nextEpisodePanel = document.querySelector('#nextEpisode');
const playNextButton = document.querySelector('#playNext');
let nextEpisode = null;
let nextEpisodeTimer = null;
let nextEpisodeLookup = null;
let activePlaylistId = '';
let activePlaylistPosition = -1;
let endCardRevision = 0;
let completionReported = false;
let playbackDuration = 0;
let playbackPoll;

function clearNextEpisode() {
  clearInterval(nextEpisodeTimer);
  nextEpisodeTimer = null;
  nextEpisode = null;
  nextEpisodePanel.hidden = true;
  movie.controls = true;
}

async function startNextEpisode() {
  if (!nextEpisode || playNextButton.disabled) return;
  const episode = nextEpisode;
  const generation = playerGeneration;
  clearInterval(nextEpisodeTimer);
  playNextButton.disabled = true;
  document.querySelector('#nextEpisodeCountdown').textContent = 'Préparation de l’épisode suivant…';
  try {
    let source = episode;
    if (!source.media_url) {
      const response = await fetch('/api/play', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: episode.id, start: episode.start || 0 }) });
      const result = await response.json();
      if (!result.ok) throw Error(result.error || 'Impossible de lire l’épisode suivant.');
      source = { ...episode, ...result, id: result.id || episode.id, detail_id: episode.id };
    }
    if (generation !== playerGeneration) return;
    openPlayer(source);
  } catch (error) {
    if (generation === playerGeneration) document.querySelector('#nextEpisodeCountdown').textContent = error.message;
  } finally { playNextButton.disabled = false; }
}

function lookupNextEpisode() {
  if (!nextEpisodeLookup) {
    const id = activeId;
    const context = activePlaylistId ? '&playlist=' + encodeURIComponent(activePlaylistId) + '&position=' + activePlaylistPosition : '';
    nextEpisodeLookup = fetch('/api/next?id=' + encodeURIComponent(id) + context)
      .then(response => response.ok ? response.json() : { next: null })
      .then(result => result.next || null).catch(() => null);
  }
  return nextEpisodeLookup;
}

function cancelNextEpisode() {
  endCardRevision++;
  clearInterval(nextEpisodeTimer);
  nextEpisodeTimer = null;
  document.querySelector('#cancelNext').hidden = true;
  document.querySelector('#nextEpisodeCountdown').textContent = 'La lecture reste arrêtée. Choisis quand continuer.';
}

function autoplayEnabled() {
  try { return localStorage.getItem('fauteuil.autoplayNext') !== 'false'; }
  catch { return true; }
}

function scheduleNextEpisode(episode) {
  clearNextEpisode();
  nextEpisode = episode;
  document.querySelector('#nextEpisodeTitle').textContent = episode ? 'À suivre' : 'Lecture terminée';
  document.querySelector('#nextEpisodeName').textContent = episode?.title || 'Prends le temps de choisir la suite.';
  const cover = document.querySelector('#nextEpisodeCover');
  cover.hidden = !episode?.thumbnail;
  if (episode?.thumbnail) cover.src = episode.thumbnail;
  else cover.removeAttribute('src');
  playNextButton.hidden = !episode;
  playNextButton.disabled = false;
  const checkbox = document.querySelector('#autoplayNext');
  checkbox.checked = autoplayEnabled();
  document.querySelector('#autoplayLabel').hidden = !episode;
  document.querySelector('#cancelNext').hidden = true;
  document.querySelector('#nextEpisodeCountdown').textContent = '';
  document.querySelector('#replayVideo').disabled = false;
  const suggestions = [...allItems, ...subscriptions, ...trending];
  const seen = new Set([activeId]);
  document.querySelector('#endSuggestions').innerHTML = suggestions.filter(item => {
    const id = contentKey(item);
    if (!id || seen.has(id) || item.id === activeId || item.id === episode?.id) return false;
    seen.add(id);
    return Boolean(item.thumbnail);
  }).slice(0, 3).map(item => `<a href="${esc(detailLink(item.id))}"><img src="${esc(item.thumbnail)}" alt=""><span>${esc(item.title)}</span></a>`).join('');
  nextEpisodePanel.hidden = false;
  if (movie.webkitDisplayingFullscreen) {
    try { movie.webkitExitFullscreen?.(); } catch {}
  }
  movie.controls = false;
  (episode ? playNextButton : document.querySelector('#replayVideo')).focus({ preventScroll: true });
  if (!episode || !checkbox.checked) return;
  const deadline = Date.now() + 15000;
  document.querySelector('#cancelNext').hidden = false;
  const countdown = document.querySelector('#nextEpisodeCountdown');
  const update = () => {
    const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    if (seconds === 0) startNextEpisode();
    else countdown.textContent = 'Épisode suivant dans ' + seconds + ' s';
  };
  update();
  nextEpisodeTimer = setInterval(update, 200);
}

playNextButton.addEventListener('click', startNextEpisode);
document.querySelector('#cancelNext').addEventListener('click', cancelNextEpisode);
document.querySelector('#autoplayNext').addEventListener('change', event => {
  try { localStorage.setItem('fauteuil.autoplayNext', String(event.target.checked)); } catch {}
  if (event.target.checked && nextEpisode) scheduleNextEpisode(nextEpisode);
  else cancelNextEpisode();
});
document.querySelector('#replayVideo').addEventListener('click', async event => {
  cancelNextEpisode();
  const generation = playerGeneration;
  const id = activeId;
  event.currentTarget.disabled = true;
  document.querySelector('#nextEpisodeCountdown').textContent = 'Préparation de la lecture…';
  try {
    const response = await fetch('/api/play', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, start: 0 }) });
    const result = await response.json();
    if (generation !== playerGeneration) return;
    if (!result.ok || !result.media_url) throw Error(result.error || 'Impossible de relancer la vidéo.');
    openPlayer({ ...result, id: result.id || id, start: 0, detail_id: activeDetailId, playlist_id: activePlaylistId, playlist_position: activePlaylistPosition });
  } catch (error) {
    if (generation === playerGeneration) {
      document.querySelector('#nextEpisodeCountdown').textContent = error.message;
      document.querySelector('#replayVideo').disabled = false;
    }
  }
});
document.querySelector('#stopAfterEpisode').addEventListener('click', () => closePlayer({ toCatalogue: true }));


function showPlayerError(message) {
  playerError.textContent = message;
  playerError.hidden = false;
}

function reportProgress({ closing = false, ended = false } = {}) {
  if (!activePath || !Number.isFinite(playbackDuration || movie.duration) || (playbackDuration || movie.duration) <= 0) {
    return progressRequest;
  }
  const body = JSON.stringify({
    id: activeId, path: activePath, time: movie.currentTime,
    duration: playbackDuration || movie.duration, closing, ended
  });
  progressRequest = progressRequest.then(async () => {
    const attempts = closing || ended ? 2 : 1;
    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        const response = await fetch('/api/progress', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
          keepalive: closing || ended
        });
        if (response.ok) return await response.json();
      } catch {}
    }
    return null;
  });
  return progressRequest;
}

function finishPlayback() {
  clearTimeout(playbackPoll);
  document.querySelector('#bufferStatus').hidden = true;
  movie.pause();
  const saved = completionReported ? progressRequest : reportProgress({ closing: true, ended: movie.ended });
  clearNextEpisode();
  playerGeneration++;
  activeId = '';
  activePath = '';
  return saved;
}

window.savePlaybackBeforeQuit = finishPlayback;

let activeDetailId = '';
let recoveringStream = false;

async function closePlayer({ toCatalogue = false } = {}) {
  const id = activeDetailId;
  const params = new URLSearchParams({ id });
  if (location.hash) params.set('from', location.hash);
  if (activePlaylistId) {
    params.set('playlist', activePlaylistId);
    params.set('position', activePlaylistPosition);
  }
  const saved = finishPlayback();
  await leavePlayerFullscreen();
  movie.removeAttribute('src');
  movie.load();
  playerError.hidden = true;
  player.classList.remove('open');
  lastPlayerPointer = null;
  revealPlayerControls();
  await saved;
  if (!toCatalogue && id) location.href = '/detail?' + params;
  else load();
}

function openPlayer({ id, path, media_url, start = 0, duration = 0, detail_id = id, playlist_id = '', playlist_position = -1 }) {
  activeDetailId = detail_id;
  recoveringStream = false;
  activePlaylistId = playlist_id;
  activePlaylistPosition = Number(playlist_position);
  clearNextEpisode();
  nextEpisodeLookup = null;
  playbackDuration = Number(duration) || 0;
  clearTimeout(playbackPoll);
  const bufferStatus = document.querySelector('#bufferStatus');
  bufferStatus.hidden = true;
  const generation = ++playerGeneration;
  completionReported = false;
  activeId = id;
  activePath = path;
  lastReport = 0;
  playerError.hidden = true;
  movie.querySelectorAll('track').forEach(track => track.remove());
  movie.onloadedmetadata = () => {
    if (start >= 0 && start < movie.duration) movie.currentTime = start;
  };
  if (path) {
    const track = document.createElement('track');
    track.kind = 'captions';
    track.srclang = 'fr';
    track.label = 'Français';
    track.src = '/api/subtitle?path=' + encodeURIComponent(path);
    movie.appendChild(track);
  }
  if (media_url.startsWith('/api/stream/')) {
    const poll = async () => {
      if (generation !== playerGeneration) return;
      try {
        const response = await fetch('/api/download/status?id=' + encodeURIComponent(id));
        const state = await response.json();
        if (generation !== playerGeneration) return;
        bufferStatus.hidden = !state.active;
        if (state.error) { await recoverProgressivePlayback(); return; }
        if (!state.active) return;
        bufferStatus.textContent = Number.isFinite(state.percent)
          ? 'Téléchargement · ' + Math.floor(state.percent) + ' %' : 'Téléchargement en cours…';
      } catch {}
      if (generation === playerGeneration && !recoveringStream) playbackPoll = setTimeout(poll, 1500);
    };
    playbackPoll = setTimeout(poll, 500);
  }
  movie.src = media_url;
  player.classList.add('open');
  revealPlayerControls();
  movie.focus({ preventScroll: true });
  movie.play().catch(error => {
    if (generation === playerGeneration && error.name === 'NotAllowedError') {
      showPlayerError('Appuie sur Lecture pour démarrer la vidéo.');
    }
  });
}

async function recoverProgressivePlayback() {
  if (!activePath || !(movie.currentSrc || movie.src || '').includes('/api/stream/')) return false;
  if (recoveringStream) return true;
  recoveringStream = true;
  clearTimeout(playbackPoll);
  const generation = playerGeneration;
  const start = movie.currentTime || 0;
  showPlayerError('Préparation de la copie locale… La lecture reprendra automatiquement.');
  try {
    const response = await fetch('/api/play', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: activeId, start, wait_for_file: true })
    });
    const source = await response.json();
    if (generation !== playerGeneration) return true;
    if (!source.ok || !source.media_url) throw Error(source.error || 'Impossible de préparer la vidéo.');
    openPlayer({ ...source, id: source.id || activeId, start, detail_id: activeDetailId,
      playlist_id: activePlaylistId, playlist_position: activePlaylistPosition });
  } catch (error) {
    if (generation === playerGeneration) showPlayerError(error.message);
  }
  return true;
}

movie.addEventListener('error', async () => {
  if (!activePath || await recoverProgressivePlayback()) return;
  const messages = {
    2: 'Le fichier vidéo n’a pas pu être chargé.',
    3: 'Le lecteur n’a pas pu décoder ce fichier vidéo.',
    4: 'Ce format vidéo n’est pas pris en charge.'
  };
  showPlayerError(messages[movie.error?.code] || 'La vidéo n’a pas pu être lue.');
});
movie.addEventListener('playing', () => {
  playerError.hidden = true;
  revealPlayerControls();
});
movie.addEventListener('timeupdate', () => {
  const duration = playbackDuration || movie.duration;
  if (!completionReported && !movie.ended && Number.isFinite(duration) && duration - movie.currentTime <= 20) lookupNextEpisode();
  if (completionReported || movie.ended || Date.now() - lastReport < 2000) return;
  lastReport = Date.now();
  reportProgress();
});
movie.addEventListener('ended', async () => {
  if (!activeId || completionReported) return;
  const shortStream = playbackDuration && movie.currentTime < playbackDuration - 2;
  let saved;
  if (shortStream) {
    const generation = playerGeneration;
    saved = await reportProgress({ ended: true });
    if (generation !== playerGeneration) return;
    if (!saved?.completed) {
      if (!await recoverProgressivePlayback()) showPlayerError('Le téléchargement s’est interrompu avant la fin de la vidéo.');
      return;
    }
  }
  completionReported = true;
  clearTimeout(playbackPoll);
  document.querySelector('#bufferStatus').hidden = true;
  const generation = playerGeneration;
  // Show the end card immediately, even if the next-episode lookup is slow or offline.
  scheduleNextEpisode(null);
  const revision = endCardRevision;
  const next = lookupNextEpisode();
  if (!saved) await reportProgress({ ended: true });
  if (generation !== playerGeneration) return;
  const episode = await next;
  if (generation === playerGeneration && revision === endCardRevision && episode) scheduleNextEpisode(episode);
});

document.addEventListener('keydown', event => {
  if (event.defaultPrevented || event.isComposing || event.metaKey || event.ctrlKey || event.altKey) return;
  if (document.querySelector('dialog[open]')) return;
  const modal = document.querySelector('.modal.open');
  if (event.key === 'Escape' && modal) {
    modal.classList.remove('open');
    event.preventDefault();
    return;
  }
  if (!player.classList.contains('open')) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    if (movie.webkitDisplayingFullscreen) movie.webkitExitFullscreen?.();
    else if (playerIsFullScreen() || enteringFullScreen) leavePlayerFullscreen();
    else closePlayer();
    return;
  }
  const key = event.key.toLowerCase();
  if (!event.target.closest?.('input, textarea, select, [contenteditable]') && key === 'f' && !event.repeat) {
    event.preventDefault();
    togglePlayerFullscreen();
    return;
  }
  if (event.target.closest?.('input, textarea, select, button, a, [contenteditable]') || completionReported) return;
  if (key === ' ' && !event.repeat) {
    event.preventDefault();
    if (movie.paused) movie.play().catch(() => {});
    else movie.pause();
  } else if ((key === 'arrowleft' || key === 'arrowright') && Number.isFinite(movie.duration)) {
    event.preventDefault();
    const offset = key === 'arrowleft' ? -10 : 10;
    movie.currentTime = Math.max(0, Math.min(movie.duration, movie.currentTime + offset));
  }
});

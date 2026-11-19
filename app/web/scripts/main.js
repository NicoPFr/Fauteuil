const playbackParams = new URLSearchParams(location.search);
if (playbackParams.has('play')) {
  openPlayer({
    id: playbackParams.get('id'), detail_id: playbackParams.get('detail') || playbackParams.get('id'), path: playbackParams.get('path'),
    playlist_id: playbackParams.get('playlist') || '', playlist_position: Number(playbackParams.get('position') ?? -1),
    duration: Number(playbackParams.get('duration') || 0),
    media_url: playbackParams.get('play'), start: Number(playbackParams.get('start') || 0)
  });
  history.replaceState({}, '', '/' + location.hash);
}
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  load();
  if (mode === 'offline') showTripStatus();
});
load();
if (playbackParams.get('focus') === 'search') window.focusCinemaSearch();
if (mode === 'offline') showTripStatus();
if (mode === 'playlists') loadPlaylists();

// Production post-play handlers in isolation: no app, player or browser is launched.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const functions = html.slice(html.indexOf('const nextEpisodePanel ='), html.indexOf('function showPlayerError'));
const ended = html.slice(html.indexOf("movie.addEventListener('ended', async"), html.indexOf("document.addEventListener('keydown', event => {\n  if (event.defaultPrevented"));
function fixture() {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, { hidden: true, textContent: '', innerHTML: '', disabled: false,
      events: {}, addEventListener(type, callback) { this.events[type] = callback; }, focus() {}, removeAttribute(name) { delete this[name]; } });
    return elements.get(selector);
  };
  let clock = 0;
  const intervals = new Set();
  const opened = [], reported = [], errors = [], calls = [];
  const storage = new Map();
  const movie = { currentTime: 60, duration: 60, events: {}, addEventListener(type, callback) { this.events[type] = callback; } };
  const context = { document: { querySelector: element }, movie, allItems: [], subscriptions: [], trending: [],
    activeId: 'current', playerGeneration: 1, activePath: '/test.mp4',
    esc: text => String(text), detailLink: id => '/detail?id=' + id, contentKey: item => item.id,
    Date: { now: () => clock }, setInterval(callback) { intervals.add(callback); return callback; }, clearInterval(callback) { intervals.delete(callback); }, clearTimeout() {},
    localStorage: { getItem(key) { return storage.get(key) ?? null; }, setItem(key, value) { storage.set(key, value); } },
    openPlayer(source) { opened.push(source); context.playerGeneration++; }, closePlayer() { calls.push('close'); },
    reportProgress(options) { reported.push(options); return Promise.resolve(); },
    recoverProgressivePlayback: async () => false, activeDetailId: 'current',
    showPlayerError(error) { errors.push(error); }, fetch: async () => ({ ok: true, json: async () => ({ next: null }) }) };
  vm.createContext(context); vm.runInContext(functions + ended, context);
  return { context, element, opened, reported, errors, movie, storage, intervals, calls,
    advance(ms) { clock += ms; for (const callback of [...intervals]) callback(); } };
}
(async () => {
  const end = fixture();
  await end.movie.events.ended();
  assert.equal(end.element('#nextEpisode').hidden, false);
  assert.equal(end.element('#nextEpisodeTitle').textContent, 'Lecture terminée');
  assert.equal(end.element('#playNext').hidden, true);
  assert.deepEqual(end.calls, []); // Ending a standalone video never closes the player.
  assert.equal(end.reported[0].ended, true);

  const series = fixture();
  series.context.episode = { id: 'next', title: 'Episode 2', media_url: '/local.mp4' };
  vm.runInContext('scheduleNextEpisode(episode)', series.context);
  assert.match(series.element('#nextEpisodeCountdown').textContent, /15 s/);
  series.advance(14999); assert.equal(series.opened.length, 0);
  series.advance(1); assert.equal(series.opened.length, 1);

  const cancelled = fixture(); cancelled.context.episode = series.context.episode;
  vm.runInContext('scheduleNextEpisode(episode); cancelNextEpisode()', cancelled.context);
  cancelled.advance(20000);
  assert.equal(cancelled.opened.length, 0);
  assert.equal(cancelled.element('#nextEpisode').hidden, false);

  const disabled = fixture(); disabled.storage.set('fauteuil.autoplayNext', 'false');
  disabled.context.episode = series.context.episode;
  vm.runInContext('scheduleNextEpisode(episode)', disabled.context);
  disabled.advance(20000); assert.equal(disabled.opened.length, 0);
  assert.equal(disabled.element('#autoplayNext').checked, false);

  const interrupted = fixture();
  vm.runInContext('playbackDuration = 120', interrupted.context);
  await interrupted.movie.events.ended();
  assert.equal(interrupted.reported.length, 1); assert.equal(interrupted.errors.length, 1);

  const offline = fixture(); offline.context.fetch = async () => { throw Error('offline'); };
  await offline.movie.events.ended();
  assert.equal(offline.element('#nextEpisode').hidden, false); assert.deepEqual(offline.calls, []);

  const replay = fixture(); replay.context.fetch = async () => ({ ok: true, json: async () => ({ ok: true, path: '/fresh.mp4', media_url: '/api/stream/new/index.m3u8' }) });
  await replay.element('#replayVideo').events.click({ currentTarget: replay.element('#replayVideo') });
  assert.equal(replay.opened[0].start, 0); assert.equal(replay.opened[0].id, 'current');
  console.log('PASS: persistent end card, 15-second autoplay, cancel, saved preference, offline end, partial failure and replay');
})().catch(error => { console.error(error); process.exitCode = 1; });

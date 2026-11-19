// Exercise the production dispatch logic without starting a browser, app or window.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const playerSource = fs.readFileSync(path.join(__dirname, '../../app/web/scripts/player.js'), 'utf8');
const code = playerSource.slice(playerSource.indexOf("const movie = document.querySelector('#movie');"), playerSource.indexOf("player.addEventListener('pointermove'"));

function fixture({ native = true, bridge = true, rejected = false } = {}) {
  const calls = [];
  const movie = {
    paused: true, webkitSupportsFullscreen: native, webkitDisplayingFullscreen: false,
    webkitEnterFullscreen() {
      calls.push('video-enter');
      if (rejected) throw Error('unavailable');
      this.webkitDisplayingFullscreen = true;
    },
    webkitExitFullscreen() { calls.push('video-exit'); this.webkitDisplayingFullscreen = false; }
  };
  const player = { classList: { remove() {}, contains() { return true; }, toggle() {} },
    async requestFullscreen() { calls.push('element-enter'); document.fullscreenElement = player; } };
  const document = { fullscreenElement: null, addEventListener() {},
    querySelector(selector) { return { '#movie': movie, '#player': player, '#playerError': {} }[selector]; },
    async exitFullscreen() { calls.push('element-exit'); document.fullscreenElement = null; } };
  const window = bridge ? { webkit: { messageHandlers: { playerFullScreen: { postMessage(value) { calls.push(value); } } } } } : {};
  const sandbox = { document, window, clearTimeout() {}, setTimeout() {}, showPlayerError(message) { throw Error(message); } };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  calls.length = 0;
  return { sandbox, calls, movie, document, player };
}

(async () => {
  const native = fixture();
  await vm.runInContext('togglePlayerFullscreen()', native.sandbox);
  assert.deepEqual(native.calls, ['video-enter']); // Native video must take priority over window bridge.
  await vm.runInContext('togglePlayerFullscreen()', native.sandbox);
  assert.deepEqual(native.calls, ['video-enter', 'video-exit']);
  assert.equal(native.movie.webkitDisplayingFullscreen, false);

  const fallback = fixture({ native: false });
  await vm.runInContext('togglePlayerFullscreen()', fallback.sandbox);
  assert.deepEqual(fallback.calls, [true]);
  vm.runInContext('window.cinemaFullScreenChanged(true, false)', fallback.sandbox);
  await vm.runInContext('togglePlayerFullscreen()', fallback.sandbox);
  assert.deepEqual(fallback.calls, [true, false]);

  const rejected = fixture({ rejected: true });
  await vm.runInContext('togglePlayerFullscreen()', rejected.sandbox);
  assert.deepEqual(rejected.calls, ['video-enter', true]);

  const browser = fixture({ native: false, bridge: false });
  await vm.runInContext('togglePlayerFullscreen()', browser.sandbox);
  assert.deepEqual(browser.calls, ['element-enter']);
  await vm.runInContext('togglePlayerFullscreen()', browser.sandbox);
  assert.deepEqual(browser.calls, ['element-enter', 'element-exit']);
  console.log('PASS: native video priority, exit, unsupported/rejected fallbacks and browser fullscreen');
})().catch(error => { console.error(error); process.exitCode = 1; });

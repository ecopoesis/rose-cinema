<script>
  import { onMount } from 'svelte';
  import * as api from './api.js';

  let { ontoast } = $props();

  const MUSICKIT_SRC = 'https://js-cdn.music.apple.com/musickit/v3/musickit.js';
  const DAY_MS = 86_400_000;

  let status = $state(null);
  let busy = $state(false);
  let now = $state(Date.now());
  let music = null;
  let musicReady = null;

  async function loadStatus() {
    try {
      status = await api.fetchAppleMusicAuth();
    } catch (e) {
      status = null;
    }
  }

  onMount(() => {
    loadStatus();
    musicReady = prepareMusicKit();
    musicReady.catch(() => {});
    const tick = setInterval(() => { now = Date.now(); loadStatus(); }, 60_000);
    return () => clearInterval(tick);
  });

  function plural(n, unit) {
    return `${n} ${unit}${n === 1 ? '' : 's'}`;
  }

  function friendly(ms) {
    const minutes = Math.max(1, Math.floor(ms / 60_000));
    if (minutes >= 1440) return plural(Math.floor(minutes / 1440), 'day');
    if (minutes >= 60) return plural(Math.floor(minutes / 60), 'hour');
    return plural(minutes, 'minute');
  }

  let warning = $derived.by(() => {
    if (!status) return '';
    if (!status.authorized) return 'Apple Music is not authorized';
    const left = new Date(status.expires_at).getTime() - now;
    if (status.expired || left <= 0) return 'Apple Music Authorization expired';
    if (left >= status.warn_days * DAY_MS) return '';
    return `Apple Music Authorization expires in ${friendly(left)}`;
  });

  function loadMusicKit() {
    if (window.MusicKit) return Promise.resolve();
    return new Promise((resolve, reject) => {
      document.addEventListener('musickitloaded', () => resolve(), { once: true });
      const s = document.createElement('script');
      s.src = MUSICKIT_SRC;
      s.async = true;
      s.onerror = () => reject(new Error('Could not load Apple MusicKit'));
      document.head.appendChild(s);
    });
  }

  // Load and configure MusicKit up front: authorize() opens Apple's popup, and
  // browsers only allow that right after the click, not after network waits.
  async function prepareMusicKit() {
    const [{ developer_token }] = await Promise.all([
      api.fetchAppleMusicDeveloperToken(), loadMusicKit(),
    ]);
    await window.MusicKit.configure({
      developerToken: developer_token,
      app: { name: 'Rose Cinema', build: '1.0.0' },
    });
    music = window.MusicKit.getInstance();
    // MusicKit caches the last token per origin; drop it so Apple issues a fresh one.
    if (music.isAuthorized) await music.unauthorize();
  }

  async function reauth() {
    busy = true;
    try {
      if (!music) await musicReady;
      const userToken = await music.authorize();
      if (!userToken) throw new Error('Apple sign-in was cancelled');
      status = await api.saveAppleMusicAuth(userToken);
      music.unauthorize().catch(() => {});
      if (status.ma_synced === false) {
        ontoast?.(`Apple Music authorized, but Music Assistant wasn't updated: ${status.ma_error}`, true);
      } else {
        ontoast?.('Apple Music authorized');
      }
    } catch (e) {
      ontoast?.(`Apple Music sign-in failed: ${e.message ?? e}`, true);
      musicReady = prepareMusicKit();
      musicReady.catch(() => {});
    } finally {
      busy = false;
    }
  }
</script>

<div class="apple-auth">
  <button class="btn btn-secondary" onclick={reauth} disabled={busy}>
    {busy ? 'Waiting for Apple…' : 'Reauth Apple Music'}
  </button>
  {#if warning}
    <div class="warning">{warning}</div>
  {/if}
</div>

<style>
  .apple-auth { display: flex; flex-direction: column; align-items: flex-end; gap: 0.35rem; }
  .apple-auth .btn:disabled { cursor: wait; opacity: 0.7; }
  .warning { color: #e5484d; font-size: 0.85rem; text-align: right; }
</style>

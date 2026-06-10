<script>
  import { onMount } from 'svelte';
  import Modal from './Modal.svelte';
  import StationForm from './StationForm.svelte';
  import DJForm from './DJForm.svelte';
  import ExclusionsForm from './ExclusionsForm.svelte';
  import * as api from './api.js';

  let activeTab = $state('stations');
  let djs = $state([]);
  let stations = $state([]);

  let toastMsg = $state('');
  let toastErr = $state(false);
  let toastVisible = $state(false);
  let toastTimer;

  let modal = $state({ open: false, type: null, data: null });
  let importInput = $state(null);

  let expandedRuns = $state({});
  let stationRuns = $state({});
  let testView = $state(null);

  function showToast(msg, error = false) {
    toastMsg = msg; toastErr = error; toastVisible = true;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastVisible = false, 4000);
  }

  async function loadAll() {
    [djs, stations] = await Promise.all([api.fetchDJs(), api.fetchStations()]);
  }

  onMount(loadAll);

  function djById(id) {
    return id ? djs.find(d => d.id === id) ?? null : null;
  }

  /* ── Modal helpers ─────────────────────────────────────────── */

  function openStationForm(station = null) {
    modal = { open: true, type: 'station', data: station };
  }
  function openDJForm(dj = null) {
    modal = { open: true, type: 'dj', data: dj };
  }
  async function openExclusions() {
    try {
      const current = await api.fetchExclusions();
      modal = { open: true, type: 'exclusions', data: current.excluded_artists };
    } catch (e) { showToast(e.message, true); }
  }
  async function handleSaveExclusions(artists) {
    try {
      const saved = await api.saveExclusions(artists);
      showToast(`Global exclusions saved (${saved.excluded_artists.length} artists)`);
      closeModal();
    } catch (e) { showToast(e.message, true); }
  }
  function closeModal() {
    modal = { open: false, type: null, data: null };
  }

  /* ── Station CRUD ──────────────────────────────────────────── */

  async function handleSaveStation(data) {
    const artFile = data._artFile;
    const removeArt = data._removeArt;
    delete data._artFile;
    delete data._removeArt;
    try {
      let saved;
      if (modal.data) {
        saved = await api.updateStation(modal.data.id, data);
      } else {
        saved = await api.createStation(data);
      }
      if (artFile) {
        await api.uploadAlbumArt(saved.id, artFile);
      } else if (removeArt && modal.data) {
        await api.deleteAlbumArt(modal.data.id);
      }
      showToast(modal.data ? 'Station updated' : 'Station created');
      closeModal();
      await loadAll();
    } catch (e) { showToast(e.message, true); }
  }

  async function handleDeleteStation(s) {
    if (!confirm(`Delete station "${s.name}"?`)) return;
    try {
      await api.deleteStationById(s.id);
      showToast('Station deleted');
      await loadAll();
    } catch (e) { showToast(e.message, true); }
  }

  function progressLabel(run) {
    if (!run.progress) return 'Generating…';
    const { completed, total, current_step } = run.progress;
    const step = (current_step || '').replace(/_/g, ' ');
    if (total > 0) return `${step || 'working'} (${completed}/${total})`;
    return step || 'Generating…';
  }

  async function handleGenerate(station, btn) {
    const orig = btn.textContent;
    btn.disabled = true;
    try {
      btn.textContent = 'Starting…';
      const run = await api.generate(station.id);
      const runId = run.id;

      while (true) {
        await new Promise(r => setTimeout(r, 3000));
        const status = await api.fetchRun(runId);

        if (status.status === 'saved') {
          const songs = status.entries.filter(e => e.type === 'song').length;
          showToast(`Episode ${status.episode || '?'} saved — ${songs} tracks`);
          break;
        }
        if (status.status === 'ready') {
          showToast(`Episode ${status.episode || '?'} ready — no MA configured`);
          break;
        }
        if (status.status === 'failed') {
          showToast(status.error_message || 'Generation failed', true);
          break;
        }
        btn.textContent = progressLabel(status);
      }
    } catch (e) { showToast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = orig; }
  }

  async function handleTest(station) {
    testView = { station, loading: true, songs: [], error: null, generationSecs: null };
    try {
      const result = await api.testPlaylist(station.id);
      testView = { ...testView, loading: false, songs: result.songs, generationSecs: result.generation_secs };
    } catch (e) {
      testView = { ...testView, loading: false, error: e.message };
    }
  }

  function formatDuration(secs) {
    const m = Math.floor(secs / 60);
    const s = Math.round(secs % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  async function toggleRuns(stationId) {
    if (expandedRuns[stationId]) {
      expandedRuns = { ...expandedRuns, [stationId]: false };
      return;
    }
    try {
      const runs = await api.fetchStationRuns(stationId);
      stationRuns = { ...stationRuns, [stationId]: runs };
      expandedRuns = { ...expandedRuns, [stationId]: true };
    } catch (e) { showToast(e.message, true); }
  }

  async function handleDeleteRun(runId, stationId) {
    if (!confirm('Delete this playlist run?')) return;
    try {
      await api.deleteRun(runId);
      showToast('Playlist deleted');
      const runs = await api.fetchStationRuns(stationId);
      stationRuns = { ...stationRuns, [stationId]: runs };
    } catch (e) { showToast(e.message, true); }
  }

  function formatRunDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  /* ── DJ CRUD ───────────────────────────────────────────────── */

  async function handleSaveDJ(data) {
    try {
      if (modal.data) {
        await api.updateDJ(modal.data.id, data);
        showToast('DJ updated');
      } else {
        await api.createDJ(data);
        showToast('DJ created');
      }
      closeModal();
      await loadAll();
    } catch (e) { showToast(e.message, true); }
  }

  async function handleDeleteDJ(d) {
    const refs = stations.filter(s => s.dj_id === d.id);
    const msg = refs.length
      ? `"${d.name}" is used by ${refs.length} station(s). They will be left without a DJ. Delete anyway?`
      : `Delete DJ "${d.name}"?`;
    if (!confirm(msg)) return;
    try {
      await api.deleteDJById(d.id);
      showToast('DJ deleted');
      await loadAll();
    } catch (e) { showToast(e.message, true); }
  }

  /* ── Export / Import ───────────────────────────────────────── */

  async function handleExport() {
    try {
      const data = await api.exportAll();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'rose-cinema-export.json'; a.click();
      URL.revokeObjectURL(url);
      showToast(`Exported ${data.djs.length} DJs, ${data.stations.length} stations`);
    } catch (e) { showToast(e.message, true); }
  }

  async function handleImport(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const r = await api.importAll(data);
      showToast(`Imported: ${r.djs_created} DJs created, ${r.djs_skipped} skipped, ${r.stations_created} stations`);
      await loadAll();
    } catch (err) { showToast(err.message, true); }
    if (importInput) importInput.value = '';
  }
</script>

{#if testView}
  <div class="test-view">
    <div class="test-header">
      <button class="btn btn-sm btn-secondary" onclick={() => testView = null}>&larr; Back</button>
      <h2>Test: {testView.station.name}</h2>
    </div>
    {#if testView.loading}
      <div class="test-loading">Picking tracks&hellip;</div>
    {:else if testView.error}
      <div class="test-error">{testView.error}</div>
    {:else}
      <div class="test-meta">
        {testView.songs.length} tracks &middot;
        {formatDuration(testView.songs.reduce((a, s) => a + s.duration_secs, 0))} total &middot;
        generated in {testView.generationSecs}s
      </div>
      <ol class="test-tracks">
        {#each testView.songs as song, i}
          <li class="test-track">
            <span class="track-num">{i + 1}</span>
            <div class="track-info">
              <span class="track-title">{song.title}</span>
              <span class="track-artist">{song.artist}</span>
              {#if song.album}
                <span class="track-album">{song.album}{#if song.year} ({song.year}){/if}</span>
              {/if}
            </div>
            <span class="track-dur">{formatDuration(song.duration_secs)}</span>
            {#if song.apple_music_url}
              <a href={song.apple_music_url} target="_blank" rel="noopener" class="track-link" title="Open in Apple Music">&#9834;</a>
            {/if}
          </li>
        {/each}
      </ol>
    {/if}
  </div>
{:else}

<h1>Rose Cinema</h1>

<nav class="tabs">
  <button class="tab" class:active={activeTab === 'stations'}
          onclick={() => activeTab = 'stations'}>Stations</button>
  <button class="tab" class:active={activeTab === 'djs'}
          onclick={() => activeTab = 'djs'}>DJs</button>
</nav>

{#if activeTab === 'stations'}
  <div class="toolbar">
    <button class="btn btn-primary" onclick={() => openStationForm()}>+ New Station</button>
    <div class="spacer"></div>
    <button class="btn btn-secondary" onclick={openExclusions}>Excluded Artists</button>
    <button class="btn btn-secondary" onclick={handleExport}>Export JSON</button>
    <button class="btn btn-secondary" onclick={() => importInput?.click()}>Import JSON</button>
    <input type="file" accept=".json" bind:this={importInput} onchange={handleImport} hidden>
  </div>

  <div class="cards">
    {#if !stations.length}
      <div class="empty">No stations yet. Click "+ New Station" to create one.</div>
    {:else}
      {#each stations as s (s.id)}
        {@const dj = djById(s.dj_id)}
        <div class="card">
          <div class="card-header">
            {#if s.album_art}
              <img src="/api/stations/{s.id}/album-art" alt="" class="card-art">
            {/if}
            <div class="card-name">{s.name}</div>
            <div class="card-actions">
              <button class="btn btn-sm btn-secondary" onclick={() => openStationForm(s)}>Edit</button>
              <button class="btn btn-sm btn-danger" onclick={() => handleDeleteStation(s)}>Delete</button>
            </div>
          </div>
          {#if s.description}
            <div class="card-desc">{s.description}</div>
          {/if}
          <div class="card-meta">
            {#if s.music_source}Source: {s.music_source}<br>{/if}
            {#if s.cron_schedule}Schedule: {s.cron_schedule}<br>{/if}
            DJ: {dj ? `${dj.name} · ${dj.tts_provider} / ${dj.tts_voice_ref || dj.tts_voice_id}` : '(none)'}<br>
            {s.length_minutes} min · talk {s.dj_talk_rate.toFixed(2)}
            · babble {s.dj_babble_rate.toFixed(2)}
            · max {s.dj_max_length_secs}s
            · {s.max_playlists > 0 ? `keep ${s.max_playlists} playlists` : 'unlimited playlists'}
          </div>
          <div class="runs-toggle">
            <button class="btn btn-sm btn-secondary" onclick={() => toggleRuns(s.id)}>
              {expandedRuns[s.id] ? 'Hide' : 'Show'} Playlists
              {#if stationRuns[s.id]?.length}({stationRuns[s.id].length}){/if}
            </button>
          </div>
          {#if expandedRuns[s.id]}
            <div class="runs-list">
              {#if !stationRuns[s.id]?.length}
                <div class="runs-empty">No playlists yet.</div>
              {:else}
                {#each stationRuns[s.id] as run (run.id)}
                  <div class="run-row">
                    <div class="run-info">
                      <span class="run-date">{#if run.episode}Ep {run.episode} · {/if}{formatRunDate(run.created_at)}</span>
                      <span class="run-meta">
                        {run.track_count} tracks · <span class="run-status" class:run-failed={run.status === 'failed'}>{run.status}</span>{#if run.generation_secs} · {run.generation_secs.toFixed(0)}s{/if}
                      </span>
                    </div>
                    <button class="btn btn-sm btn-danger" onclick={() => handleDeleteRun(run.id, s.id)}>Delete</button>
                  </div>
                {/each}
              {/if}
            </div>
          {/if}
          <div class="card-footer">
            <button class="btn btn-secondary" onclick={() => handleTest(s)}>Test</button>
            <button class="btn btn-primary" onclick={(e) => handleGenerate(s, e.target)}>Generate</button>
          </div>
        </div>
      {/each}
    {/if}
  </div>

{:else}
  <div class="toolbar">
    <button class="btn btn-primary" onclick={() => openDJForm()}>+ New DJ</button>
  </div>

  <div class="cards">
    {#if !djs.length}
      <div class="empty">No DJs yet. Click "+ New DJ" to create one.</div>
    {:else}
      {#each djs as d (d.id)}
        <div class="card">
          <div class="card-header">
            <div class="card-name">{d.name}</div>
            <div class="card-actions">
              <button class="btn btn-sm btn-secondary" onclick={() => openDJForm(d)}>Edit</button>
              <button class="btn btn-sm btn-danger" onclick={() => handleDeleteDJ(d)}>Delete</button>
            </div>
          </div>
          <div class="card-meta">{d.tts_provider} · {d.tts_voice_ref || d.tts_voice_id}</div>
          {#if d.agent_md}
            <div class="card-preview">
              {d.agent_md.length > 200 ? d.agent_md.slice(0, 200) + '…' : d.agent_md}
            </div>
          {/if}
        </div>
      {/each}
    {/if}
  </div>
{/if}

{/if}

{#if modal.open}
  <Modal
    title={modal.type === 'station'
      ? (modal.data ? 'Edit Station' : 'New Station')
      : modal.type === 'exclusions'
        ? 'Global Excluded Artists'
        : (modal.data ? 'Edit DJ' : 'New DJ')}
    onclose={closeModal}
  >
    {#if modal.type === 'station'}
      <StationForm station={modal.data} {djs} onsave={handleSaveStation} oncancel={closeModal} />
    {:else if modal.type === 'exclusions'}
      <ExclusionsForm excluded={modal.data} onsave={handleSaveExclusions} oncancel={closeModal} />
    {:else}
      <DJForm dj={modal.data} onsave={handleSaveDJ} oncancel={closeModal} />
    {/if}
  </Modal>
{/if}

{#if toastVisible}
  <div class="toast" class:error={toastErr}>{toastMsg}</div>
{/if}

<style>
  h1 { font-weight: 500; letter-spacing: -0.02em; margin: 0 0 1rem; }

  .tabs { display: flex; border-bottom: 1px solid #333; margin-bottom: 1.5rem; }
  .tab { background: none; border: none; color: #888; padding: 0.65rem 1.25rem;
         cursor: pointer; border-bottom: 2px solid transparent; font: inherit; font-size: 0.95rem; }
  .tab:hover { color: #ccc; }
  .tab.active { color: #eee; border-bottom-color: #c8395a; }

  .toolbar { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
  .spacer { flex: 1; }

  .cards { display: grid; gap: 0.75rem; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 0.5rem; padding: 1rem; }
  .card-header { display: flex; justify-content: space-between; align-items: flex-start;
                 margin-bottom: 0.5rem; gap: 0.5rem; }
  .card-art { width: 48px; height: 48px; object-fit: cover; border-radius: 0.25rem; flex-shrink: 0; }
  .card-name { font-weight: 600; font-size: 1.05rem; }
  .card-actions { display: flex; gap: 0.25rem; flex-shrink: 0; }
  .card-meta { font-size: 0.85rem; color: #888; line-height: 1.7; }
  .card-desc { font-size: 0.9rem; color: #aaa; margin-bottom: 0.4rem; }
  .card-preview { font-size: 0.8rem; color: #666; margin-top: 0.5rem;
                  white-space: pre-line; max-height: 4.5em; overflow: hidden; }
  .runs-toggle { margin-top: 0.6rem; }
  .runs-list { margin-top: 0.5rem; border-top: 1px solid #2a2a2a; padding-top: 0.5rem;
               display: flex; flex-direction: column; gap: 0.35rem; }
  .runs-empty { font-size: 0.8rem; color: #666; padding: 0.25rem 0; }
  .run-row { display: flex; justify-content: space-between; align-items: center;
             padding: 0.35rem 0.5rem; background: #151515; border-radius: 0.3rem; }
  .run-info { display: flex; flex-direction: column; gap: 0.1rem; min-width: 0; }
  .run-date { font-size: 0.8rem; color: #ccc; }
  .run-meta { font-size: 0.75rem; color: #666; }
  .run-status { text-transform: capitalize; }
  .run-failed { color: #c8395a; }
  .card-footer { margin-top: 0.75rem; display: flex; justify-content: flex-end; gap: 0.5rem; }

  .empty { color: #888; padding: 2rem; text-align: center; background: #1a1a1a; border-radius: 0.5rem; }

  .toast { position: fixed; bottom: 1.5rem; left: 50%; transform: translateX(-50%);
           background: #2a2a2a; padding: 0.75rem 1.25rem; border-radius: 0.375rem;
           border: 1px solid #444; font-size: 0.875rem; z-index: 200; max-width: 90vw; }
  .toast.error { border-color: #c8395a; color: #ffb4c5; }

  .test-view { max-width: 640px; }
  .test-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; }
  .test-header h2 { font-weight: 500; font-size: 1.1rem; margin: 0; }
  .test-loading { color: #888; padding: 2rem; text-align: center; }
  .test-error { color: #ffb4c5; padding: 1rem; background: #1a1a1a; border: 1px solid #c8395a; border-radius: 0.5rem; }
  .test-meta { font-size: 0.85rem; color: #888; margin-bottom: 0.75rem; }
  .test-tracks { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.25rem; }
  .test-track { display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0.65rem;
                background: #1a1a1a; border-radius: 0.35rem; }
  .track-num { font-size: 0.8rem; color: #555; min-width: 1.5rem; text-align: right; }
  .track-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.1rem; }
  .track-title { font-size: 0.9rem; color: #eee; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .track-artist { font-size: 0.8rem; color: #999; }
  .track-album { font-size: 0.75rem; color: #666; }
  .track-dur { font-size: 0.8rem; color: #666; white-space: nowrap; }
  .track-link { color: #c8395a; text-decoration: none; font-size: 1.1rem; padding: 0 0.25rem; }
  .track-link:hover { color: #e45a7a; }
</style>

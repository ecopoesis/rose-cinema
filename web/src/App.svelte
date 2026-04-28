<script>
  import { onMount } from 'svelte';
  import Modal from './Modal.svelte';
  import StationForm from './StationForm.svelte';
  import DJForm from './DJForm.svelte';
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

  async function handleGenerate(station, btn) {
    const orig = btn.textContent;
    btn.disabled = true;
    try {
      btn.textContent = 'Generating…';
      const run = await api.generate(station.id);
      btn.textContent = 'Saving to MA…';
      const saved = await api.saveToMA(run.id);
      showToast(`Saved "${saved.playlist_name}" (${saved.queued_uris} tracks)`);
    } catch (e) { showToast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = orig; }
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

{#if modal.open}
  <Modal
    title={modal.type === 'station'
      ? (modal.data ? 'Edit Station' : 'New Station')
      : (modal.data ? 'Edit DJ' : 'New DJ')}
    onclose={closeModal}
  >
    {#if modal.type === 'station'}
      <StationForm station={modal.data} {djs} onsave={handleSaveStation} oncancel={closeModal} />
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
  .card-footer { margin-top: 0.75rem; display: flex; justify-content: flex-end; }

  .empty { color: #888; padding: 2rem; text-align: center; background: #1a1a1a; border-radius: 0.5rem; }

  .toast { position: fixed; bottom: 1.5rem; left: 50%; transform: translateX(-50%);
           background: #2a2a2a; padding: 0.75rem 1.25rem; border-radius: 0.375rem;
           border: 1px solid #444; font-size: 0.875rem; z-index: 200; max-width: 90vw; }
  .toast.error { border-color: #c8395a; color: #ffb4c5; }
</style>

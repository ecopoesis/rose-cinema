<script>
  let { station = null, djs = [], onsave, oncancel } = $props();

  // svelte-ignore state_referenced_locally
  const init = station ?? {};
  let name = $state(init.name ?? '');
  let description = $state(init.description ?? '');
  let music_source = $state(init.music_source ?? '');
  let dj_id = $state(init.dj_id ?? '');
  let length_minutes = $state(init.length_minutes ?? 60);
  let dj_talk_rate = $state(init.dj_talk_rate ?? 0.3);
  let dj_babble_rate = $state(init.dj_babble_rate ?? 0.5);
  let dj_max_length_secs = $state(init.dj_max_length_secs ?? 30);
  let max_playlists = $state(init.max_playlists ?? 0);
  let discovery_rate = $state(init.discovery_rate ?? 0.5);
  let history_runs = $state(init.history_runs ?? 3);
  let source_artists = $state((init.source_artists ?? []).join('\n'));
  let source_albums = $state((init.source_albums ?? []).join('\n'));
  let source_tracks = $state((init.source_tracks ?? []).join('\n'));
  let cron_schedule = $state(init.cron_schedule ?? '');
  let artFile = $state(null);
  let artPreview = $state(init.album_art ? `/api/stations/${init.id}/album-art` : '');
  let removeArt = $state(false);
  let saving = $state(false);
  let showSources = $state(
    !!(init.source_artists?.length || init.source_albums?.length || init.source_tracks?.length)
  );

  function handleArtChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    artFile = file;
    removeArt = false;
    artPreview = URL.createObjectURL(file);
  }

  function handleRemoveArt() {
    artFile = null;
    artPreview = '';
    removeArt = true;
  }

  function linesToList(text) {
    return text.split('\n').map(s => s.trim()).filter(Boolean);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    saving = true;
    try {
      const artists = linesToList(source_artists);
      const albums = linesToList(source_albums);
      const tracks = linesToList(source_tracks);
      await onsave({
        name: name.trim(),
        description: description.trim(),
        music_source: music_source.trim(),
        dj_id: dj_id || null,
        length_minutes: Number(length_minutes),
        dj_talk_rate: Number(dj_talk_rate),
        dj_babble_rate: Number(dj_babble_rate),
        dj_max_length_secs: Number(dj_max_length_secs),
        max_playlists: Number(max_playlists),
        discovery_rate: Number(discovery_rate),
        history_runs: Number(history_runs),
        source_artists: artists.length ? artists : null,
        source_albums: albums.length ? albums : null,
        source_tracks: tracks.length ? tracks : null,
        cron_schedule: cron_schedule.trim() || null,
        _artFile: artFile,
        _removeArt: removeArt,
      });
    } finally { saving = false; }
  }
</script>

<form onsubmit={handleSubmit}>
  <div class="modal-body">
    <div class="field">
      <label for="s-name">Name</label>
      <input id="s-name" bind:value={name} required maxlength="200">
    </div>
    <div class="field">
      <label for="s-desc">Description</label>
      <textarea id="s-desc" bind:value={description} rows="2"></textarea>
    </div>
    <div class="field">
      <label>Album Art</label>
      <div class="art-row">
        {#if artPreview}
          <img src={artPreview} alt="Album art" class="art-thumb">
        {/if}
        <input type="file" accept=".jpg,.jpeg,.png" onchange={handleArtChange}>
        {#if artPreview}
          <button type="button" class="btn btn-sm btn-danger" onclick={handleRemoveArt}>Remove</button>
        {/if}
      </div>
    </div>
    <div class="field">
      <label for="s-source">Music Source</label>
      <textarea id="s-source" bind:value={music_source} rows="2"
                placeholder="e.g. jazz vocals, Radiohead, 90s hip-hop"></textarea>
    </div>
    <div class="field">
      <button type="button" class="btn btn-sm btn-link source-toggle"
              onclick={() => showSources = !showSources}>
        {showSources ? '▾' : '▸'} Explicit Sources
      </button>
      {#if showSources}
        <div class="source-fields">
          <div class="field">
            <label for="s-src-artists">Artists <span class="hint">(one per line)</span></label>
            <textarea id="s-src-artists" bind:value={source_artists} rows="3"
                      placeholder="Bon Iver&#10;Iron & Wine"></textarea>
          </div>
          <div class="field">
            <label for="s-src-albums">Albums <span class="hint">(one per line)</span></label>
            <textarea id="s-src-albums" bind:value={source_albums} rows="2"
                      placeholder="For Emma, Forever Ago"></textarea>
          </div>
          <div class="field">
            <label for="s-src-tracks">Tracks <span class="hint">(one per line)</span></label>
            <textarea id="s-src-tracks" bind:value={source_tracks} rows="2"
                      placeholder="Skinny Love&#10;Naked As We Came"></textarea>
          </div>
        </div>
      {/if}
    </div>
    <div class="field">
      <label for="s-dj">DJ</label>
      <select id="s-dj" bind:value={dj_id}>
        <option value="">(none)</option>
        {#each djs as d}
          <option value={d.id}>{d.name}</option>
        {/each}
      </select>
    </div>
    <div class="field">
      <label for="s-len">Length (minutes)</label>
      <input id="s-len" type="number" bind:value={length_minutes} min="5" max="480">
    </div>
    <div class="field">
      <label for="s-talk">Talk Rate</label>
      <div class="range-row">
        <input id="s-talk" type="range" bind:value={dj_talk_rate} min="0" max="1" step="0.05">
        <span class="range-val">{Number(dj_talk_rate).toFixed(2)}</span>
      </div>
    </div>
    <div class="field">
      <label for="s-babble">Babble Rate</label>
      <div class="range-row">
        <input id="s-babble" type="range" bind:value={dj_babble_rate} min="0" max="1" step="0.05">
        <span class="range-val">{Number(dj_babble_rate).toFixed(2)}</span>
      </div>
    </div>
    <div class="field">
      <label for="s-discovery">Discovery</label>
      <div class="range-row">
        <input id="s-discovery" type="range" bind:value={discovery_rate} min="0" max="1" step="0.05">
        <span class="range-val">{Number(discovery_rate).toFixed(2)}</span>
      </div>
    </div>
    <div class="field">
      <label for="s-maxlen">Max DJ Segment (seconds)</label>
      <input id="s-maxlen" type="number" bind:value={dj_max_length_secs} min="5" max="120">
    </div>
    <div class="field">
      <label for="s-history">No-Repeat Window <span class="hint">(runs, 0 = off)</span></label>
      <input id="s-history" type="number" bind:value={history_runs} min="0" max="50">
    </div>
    <div class="field">
      <label for="s-maxpl">Max Playlists (0 = unlimited)</label>
      <input id="s-maxpl" type="number" bind:value={max_playlists} min="0">
    </div>
    <div class="field">
      <label for="s-cron">Auto-Generate Schedule <span class="hint">(cron)</span></label>
      <input id="s-cron" bind:value={cron_schedule}
             placeholder="e.g. 0 6 * * * (daily at 6am UTC)">
    </div>
  </div>
  <div class="modal-footer">
    <button type="button" class="btn btn-secondary" onclick={oncancel}>Cancel</button>
    <button type="submit" class="btn btn-primary" disabled={saving}>
      {saving ? 'Saving…' : 'Save'}
    </button>
  </div>
</form>

<style>
  .art-row { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
  .art-thumb { width: 64px; height: 64px; object-fit: cover; border-radius: 0.25rem; border: 1px solid #333; }
  .source-toggle { padding: 0; color: var(--text-muted, #999); font-size: 0.85rem; text-decoration: none; background: none; border: none; cursor: pointer; }
  .source-toggle:hover { color: var(--text, #ccc); }
  .source-fields { margin-top: 0.5rem; padding-left: 0.5rem; border-left: 2px solid var(--border, #333); }
  .source-fields .field { margin-bottom: 0.5rem; }
  .hint { font-size: 0.75rem; color: var(--text-muted, #888); }
</style>

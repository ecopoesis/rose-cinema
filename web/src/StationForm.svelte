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
  let saving = $state(false);

  async function handleSubmit(e) {
    e.preventDefault();
    saving = true;
    try {
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
      <label for="s-source">Music Source</label>
      <textarea id="s-source" bind:value={music_source} rows="2"
                placeholder="e.g. jazz vocals, Radiohead, 90s hip-hop"></textarea>
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
      <label for="s-maxlen">Max DJ Segment (seconds)</label>
      <input id="s-maxlen" type="number" bind:value={dj_max_length_secs} min="5" max="120">
    </div>
    <div class="field">
      <label for="s-maxpl">Max Playlists (0 = unlimited)</label>
      <input id="s-maxpl" type="number" bind:value={max_playlists} min="0">
    </div>
  </div>
  <div class="modal-footer">
    <button type="button" class="btn btn-secondary" onclick={oncancel}>Cancel</button>
    <button type="submit" class="btn btn-primary" disabled={saving}>
      {saving ? 'Saving…' : 'Save'}
    </button>
  </div>
</form>

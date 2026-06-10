<script>
  let { excluded = [], onsave, oncancel } = $props();

  // svelte-ignore state_referenced_locally
  let text = $state(excluded.join('\n'));
  let saving = $state(false);

  async function handleSubmit(e) {
    e.preventDefault();
    saving = true;
    try {
      const artists = text.split('\n').map(s => s.trim()).filter(Boolean);
      await onsave(artists);
    } finally { saving = false; }
  }
</script>

<form onsubmit={handleSubmit}>
  <div class="modal-body">
    <div class="field">
      <label for="x-artists">Excluded Artists <span class="hint">(one per line)</span></label>
      <textarea id="x-artists" bind:value={text} rows="8"
                placeholder="Drake"></textarea>
      <p class="note">
        These artists never appear on any station — not even as a featured
        guest or collaborator. Each station can add its own exclusions on top.
      </p>
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
  .hint { font-size: 0.75rem; color: var(--text-muted, #888); }
  .note { font-size: 0.8rem; color: #888; margin: 0.5rem 0 0; }
</style>

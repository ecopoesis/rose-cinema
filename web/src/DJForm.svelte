<script>
  let { dj = null, onsave, oncancel } = $props();

  const PIPER_VOICES = [
    'en_US-lessac-medium','cori-high','kristin','bryce','norman','mv2','jenny'];

  // svelte-ignore state_referenced_locally
  const init = dj ?? {};
  let name = $state(init.name ?? '');
  let tts_provider = $state(init.tts_provider ?? 'piper');
  let tts_voice_id = $state(init.tts_voice_id ?? 'en_US-lessac-medium');
  let tts_voice_ref = $state(init.tts_voice_ref ?? '');
  let agent_md = $state(init.agent_md ?? '');
  let saving = $state(false);

  let voicePlaceholder = $derived(
    ({ piper: 'Piper voice name', elevenlabs: 'ElevenLabs voice ID',
       openai: 'alloy, nova, shimmer…', chatterbox: 'Emily.wav' })[tts_provider] || ''
  );

  async function handleSubmit(e) {
    e.preventDefault();
    saving = true;
    try {
      await onsave({
        name: name.trim(),
        tts_provider,
        tts_voice_id: tts_voice_id.trim() || 'en_US-lessac-medium',
        tts_voice_ref: tts_voice_ref.trim(),
        agent_md,
      });
    } finally { saving = false; }
  }
</script>

<form onsubmit={handleSubmit}>
  <div class="modal-body">
    <div class="field">
      <label for="d-name">Name</label>
      <input id="d-name" bind:value={name} required maxlength="100">
    </div>
    <div class="field-row">
      <div class="field">
        <label for="d-provider">TTS Provider</label>
        <select id="d-provider" bind:value={tts_provider}>
          <option value="piper">Piper</option>
          <option value="chatterbox">Chatterbox</option>
          <option value="elevenlabs">ElevenLabs</option>
          <option value="openai">OpenAI</option>
        </select>
      </div>
      <div class="field">
        <label for="d-voice">Voice</label>
        <input id="d-voice" bind:value={tts_voice_id} list="piper-voices"
               placeholder={voicePlaceholder}>
        <datalist id="piper-voices">
          {#each PIPER_VOICES as v}
            <option value={v}></option>
          {/each}
        </datalist>
      </div>
    </div>
    {#if tts_provider === 'chatterbox'}
      <div class="field">
        <label for="d-voiceref">Voice Reference (mp3/wav filename for cloning, optional)</label>
        <input id="d-voiceref" bind:value={tts_voice_ref}
               placeholder="e.g. my_voice.wav — must exist on Chatterbox server">
      </div>
    {/if}
    <div class="field">
      <label for="d-agent">Personality (Markdown)</label>
      <textarea id="d-agent" bind:value={agent_md} rows="15"
                placeholder={"Describe the DJ's personality, style, and rules\n\ne.g. You are Velvet, a late-night host..."}></textarea>
    </div>
  </div>
  <div class="modal-footer">
    <button type="button" class="btn btn-secondary" onclick={oncancel}>Cancel</button>
    <button type="submit" class="btn btn-primary" disabled={saving}>
      {saving ? 'Saving…' : 'Save'}
    </button>
  </div>
</form>

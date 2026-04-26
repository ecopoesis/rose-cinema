<script>
  let { title, onclose, children } = $props();

  function handleBackdrop(e) {
    if (e.target === e.currentTarget) onclose();
  }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') onclose(); }} />

<!-- svelte-ignore a11y_interactive_supports_focus a11y_click_events_have_key_events -->
<div class="backdrop" onclick={handleBackdrop} onkeydown={(e) => { if (e.key === 'Escape') onclose(); }} role="dialog" aria-modal="true" tabindex="-1">
  <div class="modal">
    <div class="modal-header">
      <h2>{title}</h2>
      <button class="close-btn" onclick={onclose}>&times;</button>
    </div>
    {@render children()}
  </div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.7);
              display: flex; align-items: center; justify-content: center; z-index: 100; }
  .modal { background: #1e1e1e; border: 1px solid #333; border-radius: 0.75rem;
           width: min(560px, 95vw); max-height: 90vh; display: flex; flex-direction: column; }
  .modal-header { display: flex; justify-content: space-between; align-items: center;
                  padding: 1rem 1.25rem; border-bottom: 1px solid #333; }
  .modal-header h2 { margin: 0; font-size: 1.1rem; font-weight: 500; }
  .close-btn { background: none; border: none; color: #888; font-size: 1.4rem;
               cursor: pointer; padding: 0 0.25rem; line-height: 1; }
  .close-btn:hover { color: #eee; }
  .modal :global(form) { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
  .modal :global(.modal-body) { overflow-y: auto; padding: 1rem 1.25rem; flex: 1; min-height: 0; }
  .modal :global(.modal-footer) { padding: 0.75rem 1.25rem; border-top: 1px solid #333; flex-shrink: 0; }
</style>

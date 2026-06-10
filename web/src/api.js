async function api(path, { method = 'GET', body } = {}) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = typeof body === 'string' ? body : JSON.stringify(body);
  }
  const r = await fetch(`/api${path}`, opts);
  if (!r.ok) throw new Error(await r.text());
  if (r.status === 204) return null;
  return r.json();
}

export const fetchDJs = () => api('/djs');
export const fetchStations = () => api('/stations');
export const createDJ = (data) => api('/djs', { method: 'POST', body: data });
export const updateDJ = (id, data) => api(`/djs/${id}`, { method: 'PUT', body: data });
export const deleteDJById = (id) => api(`/djs/${id}`, { method: 'DELETE' });
export const createStation = (data) => api('/stations', { method: 'POST', body: data });
export const updateStation = (id, data) => api(`/stations/${id}`, { method: 'PUT', body: data });
export const deleteStationById = (id) => api(`/stations/${id}`, { method: 'DELETE' });
export const exportAll = () => api('/export');
export const importAll = (data) => api('/import', { method: 'POST', body: data });
export const generate = (stationId) =>
  api('/generate', { method: 'POST', body: { station_id: stationId } });
export const fetchRun = (runId) => api(`/runs/${runId}`);
export const saveToMA = (runId) =>
  api(`/runs/${runId}/save-to-ma`, { method: 'POST', body: {} });
export const fetchStationRuns = (stationId) =>
  api(`/stations/${stationId}/runs`);
export const deleteRun = (runId) =>
  api(`/runs/${runId}`, { method: 'DELETE' });
export const testPlaylist = (stationId) =>
  api(`/stations/${stationId}/test-playlist`, { method: 'POST' });
export const fetchExclusions = () => api('/settings/exclusions');
export const saveExclusions = (excludedArtists) =>
  api('/settings/exclusions', { method: 'PUT', body: { excluded_artists: excludedArtists } });

export async function uploadAlbumArt(stationId, file) {
  const form = new FormData();
  form.append('file', file);
  const r = await fetch(`/api/stations/${stationId}/album-art`, { method: 'POST', body: form });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export const deleteAlbumArt = (stationId) =>
  api(`/stations/${stationId}/album-art`, { method: 'DELETE' });

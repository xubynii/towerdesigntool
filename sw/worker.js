// Runs one Sun & Wind Studio engine (sun, wind, rain, energy) off the page's thread.
// In: { meta: true } or { job, id, inputs, params }. Out: { meta } or { job, out } or { job, error }.
importScripts('engines.js');
self.onmessage = (e) => {
  const m = e.data;
  if (m.meta) {
    const settings = {}, defaults = {};
    SWX.IDS.forEach((id) => { settings[id] = SWX.settingsOf(id); defaults[id] = SWX.defaultsOf(id); });
    self.postMessage({ meta: { settings, defaults } });
    return;
  }
  try {
    const def = SWX.RUNNERS[m.id]; if (!def) throw new Error('No such engine: ' + m.id);
    const t0 = Date.now();
    const out = def.run(Object.assign({ climate: SWX.climate }, m.inputs), Object.assign({}, def.defaults, m.params || {}));
    out.ms = Date.now() - t0;
    self.postMessage({ job: m.job, out });
  } catch (err) { self.postMessage({ job: m.job, error: err && err.message ? err.message : String(err) }); }
};

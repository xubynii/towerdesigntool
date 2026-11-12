// Runs one engine off the page's thread: the Sun & Wind Studio engines (engines.js: sun, wind, rain, energy) and the
// workspace's own (daylight.js: LEED daylight). In: { meta: true } or { job, id, inputs, params }. Out: { meta } or { job, out } or { job, error }.
importScripts('engines.js', 'daylight.js');
const SETS = [SWX, TWX];
self.onmessage = (e) => {
  const m = e.data;
  if (m.meta) {
    const settings = {}, defaults = {};
    SETS.forEach((X) => X.IDS.forEach((id) => { settings[id] = X.settingsOf(id); defaults[id] = X.defaultsOf(id); }));
    self.postMessage({ meta: { settings, defaults } });
    return;
  }
  try {
    const X = SETS.find((q) => q.RUNNERS[m.id]); if (!X) throw new Error('No such engine: ' + m.id);
    const def = X.RUNNERS[m.id], t0 = Date.now();
    const out = def.run(Object.assign({ climate: SWX.climate }, m.inputs), Object.assign({}, def.defaults, m.params || {}));
    out.ms = Date.now() - t0;
    self.postMessage({ job: m.job, out });
  } catch (err) { self.postMessage({ job: m.job, error: err && err.message ? err.message : String(err) }); }
};

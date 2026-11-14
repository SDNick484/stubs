// Loads the vendored Leaflet and Leaflet.markercluster once, as classic scripts and stylesheets from /static/vendor/
// (allowed by script-src and style-src 'self'). loadLeaflet() resolves with window.L; a failed load rejects and the
// next call tries again. No DOM at import.
const BASE = "/static/vendor";
const STYLES = [`${BASE}/leaflet/leaflet.css`, `${BASE}/markercluster/MarkerCluster.css`,
  `${BASE}/markercluster/MarkerCluster.Default.css`];
const SCRIPTS = [`${BASE}/leaflet/leaflet.js`, `${BASE}/markercluster/leaflet.markercluster.js`];

let loading = null;

function addStyle(href) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.append(link);
}

function addScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.addEventListener("load", resolve);
    el.addEventListener("error", () => reject(new Error(`Could not load ${src}`)));
    document.head.append(el);
  });
}

export function loadLeaflet() {
  if (!loading) {
    loading = (async () => {
      STYLES.forEach(addStyle);
      for (const src of SCRIPTS) await addScript(src); // markercluster needs L, so one after the other
      return window.L;
    })();
    loading.catch(() => {
      loading = null;
    });
  }
  return loading;
}

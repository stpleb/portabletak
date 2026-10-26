// PortableTAK live map: phones, federated phones, ADS-B aircraft, AIS ships, Meshtastic
// nodes and markers from /api/map/snapshot, on an online map (Streets / Satellite) when
// this device has internet, or an offline map area stored on the drive.
import * as maplibregl from '/mapkit/maplibre-gl.mjs';

const ORIGIN = location.origin;
const STREETS = 'https://tiles.openfreemap.org/styles/liberty';
const STREETS_DARK = 'https://tiles.openfreemap.org/styles/dark';
const SATELLITE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const POLL_MS = 3000;
const KINDS = [
    ['phone', 'Phones', true], ['federated', 'Other servers', true], ['aircraft', 'Aircraft', true],
    ['ship', 'Ships', true], ['meshtastic', 'Meshtastic', true], ['marker', 'Markers', true], ['unit', 'Units', true],
];
const KIND_LABEL = { phone: 'Phone on this server', federated: 'Phone on a linked server', aircraft: 'Aircraft (ADS-B)',
    ship: 'Ship (AIS)', meshtastic: 'Meshtastic node', marker: 'Marker', unit: 'Unit', casevac: 'CASEVAC',
    alert: 'Alert', range_line: 'Range & bearing' };
// ATAK team colours (__group name).
const TEAM = { 'White': '#ffffff', 'Yellow': '#ffeb3b', 'Orange': '#ff9800', 'Magenta': '#ff00ff', 'Red': '#f44336',
    'Maroon': '#a0002a', 'Purple': '#9c27b0', 'Dark Blue': '#1a3fd1', 'Blue': '#2196f3', 'Cyan': '#00e5ff',
    'Teal': '#009688', 'Green': '#4caf50', 'Dark Green': '#1b5e20', 'Brown': '#795548' };
const AFFIL = { friend: '#63a7ff', hostile: '#ef3b3b', neutral: '#49d17d', unknown: '#e5d94c' };

const $ = (id) => document.getElementById(id);
const store = {
    get(k, d) { try { const v = localStorage.getItem('pt-map-' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('pt-map-' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

let online = false, areas = [], items = [], fitted = false;
// Dark or light road maps (online Streets and offline areas); satellite is unchanged.
let theme = store.get('theme', matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
const streets = () => theme === 'dark' ? STREETS_DARK : STREETS;
const shown = store.get('shown', Object.fromEntries(KINDS.map(([k, , on]) => [k, on])));

// ---------------------------------------------------------------- offline map files
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    if (opts.method && opts.method !== 'GET') {
        const s = await fetch('/api/auth/session', { cache: 'no-store' }).then(r => r.json()).catch(() => ({}));
        if (s.csrf) headers['X-CSRF-Token'] = s.csrf;
    }
    const r = await fetch(path, Object.assign({ cache: 'no-store' }, opts, { headers }));
    if (r.status === 401) { location.href = '/'; throw new Error('signed out'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data.detail && (data.detail.error || data.detail)) || data.error || r.statusText);
    return data;
}

async function checkOnline() {
    try {
        const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 4000);
        const r = await fetch(STREETS, { cache: 'no-store', signal: ctl.signal }); clearTimeout(t);
        return r.ok;
    } catch { return false; }
}

// ---------------------------------------------------------------- base maps
function overlayFonts() { return ORIGIN + '/mapkit/fonts/{fontstack}/{range}.pbf'; }

function offlineStyle(area) {
    const flavor = basemaps.namedFlavor(theme);
    return {
        version: 8,
        glyphs: overlayFonts(),
        sprite: ORIGIN + '/mapkit/sprites/v4/' + theme,
        sources: { protomaps: { type: 'vector', url: 'pmtiles://' + ORIGIN + '/maps/' + area.file,
            attribution: '<a href="https://protomaps.com">Protomaps</a> &copy; <a href="https://openstreetmap.org">OpenStreetMap</a>' } },
        layers: basemaps.layers('protomaps', flavor, { lang: 'en' }),
    };
}

function satelliteStyle() {
    return {
        version: 8, glyphs: overlayFonts(),
        sources: { sat: { type: 'raster', tiles: [SATELLITE], tileSize: 256, maxzoom: 19,
            attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics' } },
        layers: [{ id: 'sat', type: 'raster', source: 'sat' }],
    };
}

function blankStyle() {
    return { version: 8, glyphs: overlayFonts(), sources: {},
        layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#1b2430' } }] };
}

function areaFor(center) {
    const inside = areas.filter(a => a.ready && center.lng >= a.bbox[0] && center.lng <= a.bbox[2] && center.lat >= a.bbox[1] && center.lat <= a.bbox[3]);
    inside.sort((a, b) => (a.bbox[2] - a.bbox[0]) - (b.bbox[2] - b.bbox[0]));   // most detailed (smallest) first
    return inside[0] || areas.find(a => a.ready) || null;
}

function chooseBase(choice) {
    if (choice === 'streets') return { style: streets(), label: 'Streets (online)' };
    if (choice === 'satellite') return { style: satelliteStyle(), label: 'Satellite (online)' };
    if (choice && choice.startsWith('area:')) {
        const area = areas.find(a => a.name === choice.slice(5));
        if (area) return { style: offlineStyle(area), label: 'Offline: ' + area.name, area };
    }
    // Auto: online streets when this device has internet, else the offline area here.
    if (online) return { style: streets(), label: 'Streets (online)' };
    const area = areaFor(map ? map.getCenter() : { lng: 0, lat: 0 });
    if (area) return { style: offlineStyle(area), label: 'Offline: ' + area.name, area };
    return { style: blankStyle(), label: 'No map', empty: true };
}

function fillBaseSelect() {
    const sel = $('base'); const current = store.get('base', 'auto');
    sel.innerHTML = '';
    const add = (value, text, disabled) => { const o = document.createElement('option'); o.value = value; o.textContent = text; o.disabled = !!disabled; sel.appendChild(o); };
    add('auto', 'Auto map');
    add('streets', 'Streets (online)', !online);
    add('satellite', 'Satellite (online)', !online);
    areas.filter(a => a.ready).forEach(a => add('area:' + a.name, 'Offline: ' + a.name));
    sel.value = [...sel.options].some(o => o.value === current && !o.disabled) ? current : 'auto';
}

function notice(text) { const n = $('notice'); n.textContent = text || ''; n.style.display = text ? 'block' : 'none'; }

// ---------------------------------------------------------------- icons
function colorOf(it) {
    if (it.team && TEAM[it.team]) return TEAM[it.team];
    if (it.kind === 'phone' || it.kind === 'federated') return AFFIL.friend;
    if (it.argb) { const n = (Number(it.argb) >>> 0); return '#' + (n & 0xffffff).toString(16).padStart(6, '0'); }
    if (it.affiliation) return AFFIL[it.affiliation];
    return '#e8eef5';
}

function shapeOf(it) {
    if (it.kind === 'aircraft') return 'plane';
    if (it.kind === 'ship') return 'ship';
    if (it.kind === 'casevac') return 'cross';
    if (it.kind === 'meshtastic') return 'mesh';
    if (it.kind === 'phone' || it.kind === 'federated' || it.affiliation === 'friend') {
        return (it.course != null && (it.speed || 0) > 0.5) ? 'arrow' : 'dot';
    }
    if (it.affiliation === 'hostile') return 'diamond';
    if (it.affiliation === 'neutral') return 'square';
    if (it.affiliation === 'unknown') return 'quatrefoil';
    return 'pin';
}

function drawIcon(shape, color) {
    const s = 48, c = document.createElement('canvas'); c.width = c.height = s;
    const g = c.getContext('2d'); g.lineWidth = 3; g.strokeStyle = '#0b0f14'; g.fillStyle = color;
    const path = () => { g.fill(); g.stroke(); };
    g.beginPath();
    switch (shape) {
    case 'dot': g.arc(24, 24, 12, 0, Math.PI * 2); path(); break;
    case 'arrow': g.moveTo(24, 5); g.lineTo(38, 40); g.lineTo(24, 32); g.lineTo(10, 40); g.closePath(); path(); break;
    case 'plane': g.moveTo(24, 3); g.lineTo(28, 18); g.lineTo(44, 27); g.lineTo(44, 31); g.lineTo(28, 27); g.lineTo(27, 38);
        g.lineTo(33, 43); g.lineTo(33, 46); g.lineTo(24, 43); g.lineTo(15, 46); g.lineTo(15, 43); g.lineTo(21, 38);
        g.lineTo(20, 27); g.lineTo(4, 31); g.lineTo(4, 27); g.lineTo(20, 18); g.closePath(); path(); break;
    case 'ship': g.moveTo(24, 4); g.lineTo(34, 18); g.lineTo(34, 44); g.lineTo(14, 44); g.lineTo(14, 18); g.closePath(); path(); break;
    case 'diamond': g.moveTo(24, 4); g.lineTo(44, 24); g.lineTo(24, 44); g.lineTo(4, 24); g.closePath(); path(); break;
    case 'square': g.rect(9, 9, 30, 30); path(); break;
    case 'quatrefoil': [[24, 13], [35, 24], [24, 35], [13, 24]].forEach(([x, y]) => { g.moveTo(x + 10, y); g.arc(x, y, 10, 0, Math.PI * 2); }); path(); break;
    case 'mesh': g.arc(24, 24, 13, 0, Math.PI * 2); path(); g.beginPath(); g.fillStyle = '#0b0f14';
        [[24, 17], [17, 29], [31, 29]].forEach(([x, y]) => { g.moveTo(x + 3, y); g.arc(x, y, 3, 0, Math.PI * 2); }); g.fill(); break;
    case 'cross': g.arc(24, 24, 18, 0, Math.PI * 2); g.fillStyle = '#ffffff'; path(); g.beginPath(); g.fillStyle = '#e02424';
        g.rect(20, 11, 8, 26); g.rect(11, 20, 26, 8); g.fill(); break;
    default: g.moveTo(24, 46); g.bezierCurveTo(10, 30, 8, 22, 8, 18); g.arc(24, 18, 16, Math.PI, 0); g.bezierCurveTo(40, 22, 38, 30, 24, 46);
        path(); g.beginPath(); g.fillStyle = '#0b0f14'; g.arc(24, 18, 5, 0, Math.PI * 2); g.fill();
    }
    return g.getImageData(0, 0, s, s);
}

// ---------------------------------------------------------------- map
let map = null;
const startView = store.get('view', { center: [-97.6, 32.7], zoom: 3 });

function features() {
    return {
        type: 'FeatureCollection',
        features: items.filter(it => shown[it.kind] !== false && (shown[it.kind] !== undefined || shown.marker !== false)).map(it => ({
            type: 'Feature', geometry: { type: 'Point', coordinates: [it.lon, it.lat] },
            properties: { uid: it.uid, label: it.callsign, icon: 'pt:' + shapeOf(it) + ':' + colorOf(it),
                rotate: ['arrow', 'plane', 'ship'].includes(shapeOf(it)) ? (it.course || 0) : 0 },
        })),
    };
}

function addOverlay() {
    if (map.getSource('items')) return;
    map.addSource('items', { type: 'geojson', data: features() });
    map.addLayer({ id: 'items', type: 'symbol', source: 'items', layout: {
        'icon-image': ['get', 'icon'], 'icon-size': 0.8, 'icon-rotate': ['get', 'rotate'],
        'icon-rotation-alignment': 'map', 'icon-allow-overlap': true, 'icon-ignore-placement': true,
        'text-field': ['get', 'label'], 'text-font': ['Noto Sans Regular'], 'text-size': 12,
        'text-offset': [0, 1.5], 'text-anchor': 'top', 'text-optional': true },
        paint: { 'text-color': '#ffffff', 'text-halo-color': '#0b0f14', 'text-halo-width': 1.6 } });
}

function setBase(choice) {
    const base = chooseBase(choice);
    store.set('base', choice);
    map.setStyle(base.style);
    $('status').dataset.base = base.label;
    if (base.empty) notice(online ? '' : 'No internet on this device and no offline map yet. While online, open "Offline maps" and save the area you need.');
    else notice('');
    // An offline map only covers its saved area: picking one shows that area.
    const area = base.area, c = map.getCenter();
    if (area && choice !== 'auto' && !(c.lng >= area.bbox[0] && c.lng <= area.bbox[2] && c.lat >= area.bbox[1] && c.lat <= area.bbox[3]))
        map.fitBounds([[area.bbox[0], area.bbox[1]], [area.bbox[2], area.bbox[3]]], { duration: 500 });
    checkInside();
}

// Blank map outside the offline area: say so instead of leaving an empty screen.
function checkInside() {
    const label = $('status').dataset.base || '';
    const area = label.startsWith('Offline: ') && areas.find(a => a.name === label.slice(9));
    if (!area) return;
    const b = map.getBounds();
    const off = b.getEast() < area.bbox[0] || b.getWest() > area.bbox[2] || b.getNorth() < area.bbox[1] || b.getSouth() > area.bbox[3];
    notice(off ? 'This is outside the offline map "' + area.name + '", so there is no map here. Open "Offline maps" > Show to go to it.' : '');
}

async function refresh() {
    try {
        const data = await api('/api/map/snapshot');
        items = data.items || [];
        if (map.getSource('items')) map.getSource('items').setData(features());
        const n = items.filter(it => shown[it.kind] !== false).length;
        $('status').textContent = n + ' on map · ' + ($('status').dataset.base || '') + ' · ' + new Date().toLocaleTimeString();
        if (!fitted && items.length && !store.get('view-saved', false)) { fitAll(); fitted = true; }
    } catch (e) {
        $('status').textContent = 'Map data unavailable (' + e.message + ')';
    }
}

function fitAll() {
    const pts = items.filter(it => shown[it.kind] !== false);
    if (!pts.length) return;
    const b = new maplibregl.LngLatBounds();
    pts.forEach(it => b.extend([it.lon, it.lat]));
    map.fitBounds(b, { padding: 80, maxZoom: 15, duration: 600 });
}

function popupHtml(it) {
    const esc = (v) => String(v).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
    const rows = [];
    const row = (k, v) => { if (v !== undefined && v !== null && v !== '') rows.push('<div class="pop-row"><span>' + k + '</span><span>' + esc(v) + '</span></div>'); };
    const ago = it.time ? Math.max(0, Math.round((Date.now() - Date.parse(it.time)) / 1000)) : null;
    row('Updated', ago === null ? '' : ago < 90 ? ago + ' s ago' : Math.round(ago / 60) + ' min ago');
    row('Team', it.team ? it.team + (it.role ? ' · ' + it.role : '') : '');
    row('Position', it.lat.toFixed(5) + ', ' + it.lon.toFixed(5));
    if (it.hae !== null && it.hae !== undefined && it.hae < 9999999) row('Altitude', Math.round(it.hae * 3.28084) + ' ft');
    if (it.speed) row('Speed', (it.speed * 1.94384).toFixed(0) + ' kn');
    if (it.course !== undefined && it.course !== null) row('Course', Math.round(it.course) + '°');
    if (it.battery) row('Battery', Math.round(it.battery) + '%');
    row('Status', it.status || '');
    row('Placed by', it.parent || '');
    row('Remarks', it.remarks || '');
    return '<div class="pop-title">' + esc(it.callsign) + '</div><div class="pop-kind">' + esc(KIND_LABEL[it.kind] || it.kind) +
        ' · ' + esc(it.type) + '</div>' + rows.join('');
}

// ---------------------------------------------------------------- offline maps panel
function renderPanel() {
    const p = $('panel');
    const fmt = (b) => b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : (b / 1e6).toFixed(0) + ' MB';
    const list = areas.length ? areas.map(a => '<div class="area"><b>' + a.name + '</b><br><small>' +
        (a.ready ? fmt(a.size) + ' · detail to zoom ' + a.maxzoom : (a.error ? 'Failed: ' + a.error : 'Downloading ' + (a.progress || 0) + '%')) +
        '</small><br><button data-show="' + a.name + '">Show</button><button data-del="' + a.name + '">Delete</button></div>').join('')
        : '<p>No offline maps yet.</p>';
    p.innerHTML = '<h2>Offline maps</h2><p>Saved map areas work without internet. They come from OpenStreetMap ' +
        '(Protomaps) and are stored on this drive. Downloading needs internet on the <b>server</b>.</p>' + list +
        '<div class="area"><b>Save the area on screen</b><p>Name</p><input id="area-name" placeholder="e.g. Fort Worth">' +
        '<p>Detail</p><select id="area-zoom"><option value="13">Towns and main roads (smaller)</option>' +
        '<option value="15" selected>Streets (recommended)</option><option value="16">Buildings (largest)</option></select>' +
        '<button class="primary" id="area-save">Download this area</button><button id="area-close">Close</button>' +
        '<p id="area-msg"></p></div>';
    p.querySelectorAll('[data-show]').forEach(b => b.onclick = () => {
        const a = areas.find(x => x.name === b.dataset.show);
        map.fitBounds([[a.bbox[0], a.bbox[1]], [a.bbox[2], a.bbox[3]]], { duration: 500 });
        $('base').value = 'area:' + a.name; setBase('area:' + a.name);
    });
    p.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
        if (!confirm('Delete the offline map "' + b.dataset.del + '"?')) return;
        await api('/api/map/areas/' + encodeURIComponent(b.dataset.del), { method: 'DELETE' }).catch(e => alert(e.message));
        await loadAreas(); renderPanel();
    });
    $('area-close').onclick = () => { p.style.display = 'none'; };
    $('area-save').onclick = async () => {
        const name = $('area-name').value.trim();
        const b = map.getBounds();
        try {
            await api('/api/map/areas', { method: 'POST', body: JSON.stringify({ name,
                bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], maxzoom: Number($('area-zoom').value) }) });
            $('area-msg').textContent = 'Downloading… you can keep using the map.';
            pollAreas();
        } catch (e) { $('area-msg').textContent = e.message; }
    };
}

async function loadAreas() {
    try { areas = (await api('/api/map/areas')).areas || []; } catch { areas = []; }
    fillBaseSelect();
}

let areaTimer = null;
function pollAreas() {
    clearTimeout(areaTimer);
    areaTimer = setTimeout(async () => {
        await loadAreas();
        if ($('panel').style.display === 'block') renderPanel();
        if (areas.some(a => !a.ready && !a.error)) pollAreas();
    }, 2000);
}

// ---------------------------------------------------------------- start
async function start() {
    const filters = $('filters');
    KINDS.forEach(([k, label]) => {
        const l = document.createElement('label'); const c = document.createElement('input');
        c.type = 'checkbox'; c.checked = shown[k] !== false;
        c.onchange = () => { shown[k] = c.checked; store.set('shown', shown); if (map.getSource('items')) map.getSource('items').setData(features()); };
        l.append(c, label); filters.appendChild(l);
    });
    [online] = await Promise.all([checkOnline(), loadAreas()]);
    fillBaseSelect();
    map = new maplibregl.Map({ container: 'map', style: blankStyle(), center: startView.center, zoom: startView.zoom,
        attributionControl: { compact: true } });
    window.ptMap = map;   // for troubleshooting from the browser console
    map.on('error', (e) => console.warn('map:', e.error ? e.error.message || e.error : e));
    map.addControl(new maplibregl.NavigationControl(), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-left');
    // MapLibre 6: icons are drawn on demand ("pt:<shape>:<colour>").
    map.setMissingStyleImageResolver((id) => {
        if (!id.startsWith('pt:') || map.hasImage(id)) return;
        const [, shape, color] = id.split(':');
        map.addImage(id, drawIcon(shape, color), { pixelRatio: 2 });
    });
    map.on('style.load', addOverlay);
    map.on('moveend', () => { const c = map.getCenter(); store.set('view', { center: [c.lng, c.lat], zoom: map.getZoom() }); store.set('view-saved', true); checkInside(); });
    map.on('click', 'items', (e) => {
        const it = items.find(x => x.uid === e.features[0].properties.uid);
        if (it) new maplibregl.Popup({ maxWidth: '300px' }).setLngLat([it.lon, it.lat]).setHTML(popupHtml(it)).addTo(map);
    });
    map.on('mouseenter', 'items', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'items', () => { map.getCanvas().style.cursor = ''; });
    setBase($('base').value);
    $('base').onchange = () => setBase($('base').value);
    $('fit').onclick = fitAll;
    const themeLabel = () => { $('theme').textContent = theme === 'dark' ? '\u2600 Light' : '\u263E Dark'; };
    themeLabel();
    $('theme').onclick = () => { theme = theme === 'dark' ? 'light' : 'dark'; store.set('theme', theme); themeLabel(); setBase($('base').value); };
    $('show').onclick = () => $('filters').classList.toggle('open');
    $('offline').onclick = () => { const p = $('panel'); p.style.display = p.style.display === 'block' ? 'none' : 'block'; if (p.style.display === 'block') renderPanel(); };
    if (areas.some(a => !a.ready && !a.error)) pollAreas();
    await refresh();
    setInterval(refresh, POLL_MS);
    // Re-check internet now and then; Auto switches between online and offline maps.
    setInterval(async () => {
        const was = online; online = await checkOnline();
        if (was !== online) { fillBaseSelect(); if ($('base').value === 'auto') setBase('auto'); }
    }, 60000);
}

start();

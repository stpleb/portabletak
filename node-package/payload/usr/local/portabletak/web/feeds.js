// "Live Feeds" card: ADS-B aircraft (internet and/or a USB radio) and AIS ships (internet),
// shown on the live map and sent to phones in the ADS-B / AIS channels.
(function () {
  const call = (path, method, body) => fetch(path, {
    method: method || 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const small = 'font-size:0.9em;opacity:0.85;line-height:1.5';
  const ERRORS = {
    adsb_needs_center: 'Set the area centre first (or tap "Use my location").',
    ais_needs_source: 'Ships from the internet need an aisstream.io key (or an AISHub username).',
    'internet: set_a_center_point': 'Set the area centre.',
    'radio: no_radio_data': 'No data from the USB radio yet. Is it plugged in?',
    'radio: radio_not_updating': 'The USB radio stopped sending data.'
  };
  const why = d => (d && d.detail && (d.detail.error || d.detail)) || (d && d.error) || 'unknown error';
  const msg = e => ERRORS[e] || String(e);
  const ago = t => { const s = Math.max(0, Math.round(Date.now() / 1000 - t)); return s < 90 ? s + ' s ago' : Math.round(s / 60) + ' min ago'; };

  function check(label, note) {
    const wrap = el('label', 'display:flex;gap:10px;align-items:flex-start;justify-content:flex-start;margin:8px 0;cursor:pointer');
    const box = el('input', 'width:auto;flex:0 0 auto;margin:3px 0 0;min-height:0'); box.type = 'checkbox';
    const text = el('span', 'flex:1;text-align:left'); text.append(el('b', '', label)); if (note) text.append(el('div', small, note));
    wrap.append(box, text);
    return {wrap, box};
  }

  function build() {
    const card = el('section', 'margin-top:18px'); card.className = 'card';
    const details = el('details');
    details.append(el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Live Feeds'));
    const body = el('div', 'margin-top:14px'); details.appendChild(body); card.appendChild(details);
    body.append(el('div', 'margin:0 0 10px;' + small,
      'Aircraft (ADS-B) and ships (AIS) on the Live Map. Phones see them when their user receives the ' +
      'ADS-B or AIS channel (Channels card). Internet feeds need internet on this server.'));

    const area = el('div', 'display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;align-items:end');
    const field = (label, placeholder) => { const w = el('label', small); const i = el('input', 'width:100%;margin-top:3px'); i.placeholder = placeholder; w.append(label, i); area.appendChild(w); return i; };
    const lat = field('Centre latitude', 'e.g. 32.70'), lon = field('Centre longitude', 'e.g. -97.60'), radius = field('Radius (nautical miles)', '50');
    const locate = el('button', 'margin:2px 0', 'Use my location');
    area.appendChild(locate);
    body.append(el('div', 'font-weight:600;margin-top:4px', 'Area'), area);

    const adsbNet = check('Aircraft from the internet', 'adsb.lol (free, community data). Updates every 15 s.');
    const adsbRadio = check('Aircraft from a USB radio', 'An RTL-SDR stick with a 1090 MHz antenna. Works without internet.');
    const radioState = el('div', small + ';margin:-4px 0 6px 28px');
    const aisNet = check('Ships from the internet', 'aisstream.io: free key (sign in at aisstream.io, then API Keys). ' +
      'Or AISHub, if you share an AIS receiver with them.');
    const aisKey = el('input', 'width:min(360px,100%);margin:0 0 6px 28px'); aisKey.placeholder = 'aisstream.io API key';
    const aisUser = el('input', 'width:min(260px,100%);margin:0 0 6px 28px'); aisUser.placeholder = 'or AISHub username';
    body.append(el('div', 'font-weight:600;margin-top:12px', 'Aircraft (ADS-B)'), adsbNet.wrap, adsbRadio.wrap, radioState,
      el('div', 'font-weight:600;margin-top:12px', 'Ships (AIS)'), aisNet.wrap, aisKey, el('br'), aisUser);

    const status = el('div', 'margin:10px 0;' + small);
    const save = el('button', 'margin-top:6px', 'Save'); save.className = 'primary';
    const saved = el('span', 'margin-left:10px;font-weight:600');
    body.append(status, save, saved);

    async function load() {
      const r = await call('/api/feeds');
      if (!r.ok) { status.textContent = msg(why(r.data)); return; }
      const c = r.data.config || {}, a = c.adsb || {}, s = c.ais || {}, st = r.data.status || {};
      if (document.activeElement !== lat) lat.value = a.lat ?? '';
      if (document.activeElement !== lon) lon.value = a.lon ?? '';
      if (document.activeElement !== radius) radius.value = a.radius_nm ?? 50;
      adsbNet.box.checked = !!a.internet; adsbRadio.box.checked = !!a.radio; aisNet.box.checked = !!s.internet;
      if (document.activeElement !== aisUser) aisUser.value = s.aishub_username || '';
      if (document.activeElement !== aisKey) aisKey.value = s.aisstream_key || '';
      radioState.textContent = r.data.radio_detected ? 'USB radio detected.' : 'No USB radio plugged in.';
      const line = (name, x) => !x || x.off ? name + ': off' :
        name + ': ' + (x.count || 0) + ' on the map, ' + ago(x.time) + (x.error ? ' — ' + msg(x.error) : '');
      status.textContent = line('Aircraft', st.adsb) + '  ·  ' + line('Ships', st.ais) + '  ·  service ' + (r.data.service || '').toLowerCase();
    }

    locate.onclick = () => {
      if (!navigator.geolocation) { saved.textContent = 'This browser cannot share its location.'; return; }
      navigator.geolocation.getCurrentPosition(p => { lat.value = p.coords.latitude.toFixed(4); lon.value = p.coords.longitude.toFixed(4); },
        () => { saved.textContent = 'Location not available; type the centre instead.'; }, {timeout: 10000});
    };
    save.onclick = async () => {
      const la = parseFloat(lat.value), lo = parseFloat(lon.value), rad = parseFloat(radius.value) || 50;
      const deg = rad / 60;   // nautical miles to degrees of latitude
      const body = {adsb: {internet: adsbNet.box.checked, radio: adsbRadio.box.checked, lat: la, lon: lo, radius_nm: rad},
        ais: {internet: aisNet.box.checked, aishub_username: aisUser.value.trim(), aisstream_key: aisKey.value.trim()}};
      if (!isNaN(la) && !isNaN(lo)) {
        const dlon = deg / Math.max(0.2, Math.cos(la * Math.PI / 180));
        body.ais.bbox = [lo - dlon, la - deg, lo + dlon, la + deg];
      }
      const r = await call('/api/feeds', 'POST', body);
      saved.textContent = r.ok ? 'Saved.' : msg(why(r.data));
      setTimeout(load, 1500);
    };
    let timer = null;
    details.addEventListener('toggle', () => {
      clearInterval(timer);
      if (details.open) { load(); timer = setInterval(load, 10000); }
    });

    // After Channels (which follows Users); else at the end.
    const anchor = Array.from(document.querySelectorAll('section summary')).find(h => h.textContent.trim() === 'Channels');
    const after = anchor && anchor.closest('section');
    if (after) after.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  // Channels adds its card when the page is ready; go after it.
  const go = () => setTimeout(build, 0);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();

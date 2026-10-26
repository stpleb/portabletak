// "Meshtastic" card: link Meshtastic radios to this TAK server over MQTT. Radios (or the
// Meshtastic phone app) send positions and messages; phones in the Meshtastic channel
// see the radios, and the radios appear on the Live Map.
(function () {
  const call = (path, method, body) => fetch(path, {
    method: method || 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const small = 'font-size:0.9em;opacity:0.85;line-height:1.5';
  const ERRORS = { paste_the_channel_link: 'Paste the channel link from the Meshtastic app (it starts with https://meshtastic.org/e/#).',
    not_a_meshtastic_link: 'That is not a Meshtastic channel link.', not_found: 'That channel is already gone.' };
  const why = d => (d && d.detail && (d.detail.error || d.detail)) || (d && d.error) || 'unknown error';
  const msg = e => ERRORS[e] || String(e);

  function rows(pairs) {
    const t = el('table', 'border-collapse:collapse;margin:6px 0');
    pairs.forEach(([k, v]) => {
      const tr = el('tr');
      tr.append(el('td', 'padding:3px 12px 3px 0;opacity:0.75;white-space:nowrap;vertical-align:top', k),
        el('td', 'padding:3px 0;font-weight:600;font-family:ui-monospace,Menlo,monospace;word-break:break-all', v));
      t.appendChild(tr);
    });
    return t;
  }

  function build() {
    const card = el('section', 'margin-top:18px'); card.className = 'card';
    const details = el('details');
    details.append(el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Meshtastic'));
    const body = el('div', 'margin-top:14px'); details.appendChild(body); card.appendChild(details);
    body.append(el('div', 'margin:0 0 10px;' + small,
      'Link Meshtastic radios to this TAK server. Radios with Wi-Fi join the same network as the server; ' +
      'others go through the Meshtastic phone app ("MQTT client proxy"). Radios show on the Live Map, and phones ' +
      'whose user receives the Meshtastic channel see them in ATAK.'));
    const status = el('div', 'min-height:1.2em;margin:4px 0;font-weight:600');
    const toggle = el('button', 'margin:4px 0');
    const apply = el('button', 'margin:4px 0 4px 8px', 'Apply now (restarts the server, about 2 minutes)');
    apply.className = 'primary';
    const setup = el('div');
    const channels = el('div');
    body.append(status, toggle, apply, setup, channels);
    let state = null;

    async function refresh() {
      const r = await call('/api/meshtastic');
      if (!r.ok) { status.textContent = msg(why(r.data)); return; }
      state = r.data;
      toggle.textContent = state.enabled ? 'Turn Meshtastic off' : 'Turn Meshtastic on';
      toggle.className = state.enabled ? '' : 'primary';
      apply.style.display = state.applied ? 'none' : 'inline-block';
      status.textContent = state.enabled ? (state.applied ? 'On.' : 'Turned on: press Apply now to start it.')
        : (state.applied ? 'Off.' : 'Turned off: press Apply now to finish.');
      setup.innerHTML = ''; channels.innerHTML = '';
      if (!state.enabled) return;
      setup.append(el('div', 'font-weight:600;margin-top:12px', 'Radio settings (Meshtastic app > Settings > Modules > MQTT)'),
        rows([['MQTT enabled', 'on'], ['Server address', (state.server || 'this server') + ':' + state.port],
          ['Username', state.username], ['Password', state.password], ['Root topic', state.topic],
          ['Encryption enabled', 'on'], ['JSON output', 'off'], ['TLS', 'off']]),
        el('div', small, (state.remote ? 'Radios reaching the server over Tailscale use ' + state.remote + ':' + state.port + '. ' : '') +
          'Then, for each channel below, turn on Uplink and Downlink (Channels > channel > MQTT). ' +
          'Radio without Wi-Fi: in the Meshtastic phone app also turn on "MQTT client proxy".'));
      channels.append(el('div', 'font-weight:600;margin-top:14px', 'Channels'),
        el('div', small, 'The server reads and sends on these channels (it needs each channel’s key). ' +
          'Channel changes apply after a server restart.'));
      (state.channels || []).forEach(c => {
        const line = el('div', 'display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.08);flex-wrap:wrap');
        line.append(el('span', 'font-weight:600;min-width:120px', c.name),
          el('span', 'flex:1;' + small, c.default_key ? 'default key (public)' : (c.encrypted ? 'private key' : 'no encryption')));
        const del = el('button', '', 'Remove'); del.className = 'danger';
        del.onclick = async () => {
          if (!confirm('Remove the channel ' + c.name + '?')) return;
          const x = await call('/api/meshtastic/channels/' + c.id, 'DELETE');
          status.textContent = x.ok ? 'Removed. Applies after a restart.' : msg(why(x.data));
          refresh();
        };
        line.append(del); channels.appendChild(line);
      });
      const addRow = el('div', 'display:flex;gap:8px;flex-wrap:wrap;margin-top:8px');
      const link = el('input', 'flex:1;min-width:220px'); link.placeholder = 'Channel link from the Meshtastic app (Share)';
      const add = el('button', '', 'Add channel'); add.className = 'primary';
      add.onclick = async () => {
        const x = await call('/api/meshtastic/channels', 'POST', {url: link.value.trim()});
        status.textContent = x.ok && x.data.ok ? 'Added ' + (x.data.added || []).map(c => c.name).join(', ') + '. Applies after a restart.' : msg(why(x.data));
        if (x.ok && x.data.ok) link.value = '';
        refresh();
      };
      addRow.append(link, add); channels.appendChild(addRow);
    }

    toggle.onclick = async () => {
      const x = await call('/api/meshtastic', 'POST', {enabled: !state.enabled});
      status.textContent = x.ok ? 'Saved.' : msg(why(x.data));
      refresh();
    };
    apply.onclick = async () => {
      if (!confirm('Restart the server now? Phones reconnect by themselves in a few minutes.')) return;
      apply.disabled = true;
      status.textContent = 'Stopping…';
      await call('/api/stop', 'POST', {});
      status.textContent = 'Starting… (about 2 minutes)';
      await call('/api/start', 'POST', {});
      apply.disabled = false;
      refresh();
    };
    details.addEventListener('toggle', () => { if (details.open) refresh(); });

    const heads = Array.from(document.querySelectorAll('section summary'));
    const anchor = heads.find(h => h.textContent.trim() === 'ATAK Plugins') || heads.find(h => h.textContent.trim() === 'Live Feeds');
    const after = anchor && anchor.closest('section');
    if (after) after.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  const go = () => setTimeout(build, 40);   // after Live Feeds and ATAK Plugins
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();

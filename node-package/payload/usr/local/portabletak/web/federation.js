// "Federation" card: share positions, markers and chat with other TAK servers.
//   Invite a server / Join a server  - PortableTAK (or OpenTAKServer with this add-on): one code
//   Connect to a TAK Server          - official TAK Server: address, confirm, admin sheet
//   Linked servers list + Advanced (manual certificates and links)
(function () {
  const call = (path, method, body) => fetch('/api/federation' + path, {
    method: method || 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const why = d => (d && d.detail && (d.detail.error || d.detail)) || (d && d.error) || 'unknown error';
  const small = 'font-size:0.9em;opacity:0.85;line-height:1.5';
  const ERRORS = {
    federation_unavailable: 'Federation is not available on this server yet (update PortableTAK).',
    federation_not_running: 'Federation could not start on this server. Try again in a minute.',
    invalid_name: 'Use letters, numbers, spaces, dots, dashes or underscores for the name.',
    invalid_address: 'Enter the other server\'s address (IP or host name).',
    invalid_groups: 'Group names: letters, numbers, spaces, dots, dashes, underscores.',
    not_a_certificate: 'That file is not a PEM certificate (it should start with -----BEGIN CERTIFICATE-----).',
    not_an_invite: 'That is not a PortableTAK invite code (it starts with PTFED1.).',
    damaged_invite: 'The invite code is incomplete or damaged. Copy all of it again.',
    certificate_mismatch: 'The server at that address is not the one that made the invite.',
    server_unreachable: 'Could not reach the other server. Both servers need a network path to each other (same network, or the same Tailscale account).',
    invite_not_found: 'The other server does not know this invite (made on a different drive, or deleted).',
    invite_already_used: 'This invite was already used. Ask for a new one.',
    invite_expired: 'This invite expired (they last 24 hours). Ask for a new one.',
    no_certificate: 'That server did not present a certificate on the federation port.',
    ca_not_sent: 'That server did not send its CA certificate. Ask its admin for the CA file and use Advanced.'
  };
  const msg = e => ERRORS[e] || String(e);
  const pretty = fp => ((fp || '').toUpperCase().match(/.{2}/g) || []).join(':');
  const DIRECTIONS = [['both', 'Share both ways'], ['send', 'Send only (they see us, we do not see them)'],
                      ['receive', 'Receive only (we see them, they do not see us)']];
  function directionSelect(value) {
    const sel = el('select', 'margin:3px 0;max-width:100%');
    DIRECTIONS.forEach(([v, t]) => { const o = el('option', '', t); o.value = v; sel.appendChild(o); });
    sel.value = value || 'both';
    return sel;
  }
  const directionOf = f => {
    const i = (f.inbound_groups || []).length, o = (f.outbound_groups || []).length;
    return i && o ? 'both' : (o ? 'send' : (i ? 'receive' : 'none'));
  };
  const btn = (text, primary) => { const b = el('button', 'margin:4px 8px 4px 0', text); if (primary) b.className = 'primary'; return b; };

  function build() {
    const card = el('section', 'margin-top:18px'); card.className = 'card';
    const details = el('details');
    details.append(el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Federation'));
    const body = el('div', 'margin-top:14px'); details.appendChild(body); card.appendChild(details);

    body.append(el('div', 'margin:0 0 12px;' + small,
      'Share positions, markers and chat with other TAK servers: another PortableTAK, OpenTAKServer or an official TAK Server.'));
    const actions = el('div', 'display:flex;flex-wrap:wrap');
    const bInvite = btn('Invite a server', true), bJoin = btn('Join a server', true), bTak = btn('Connect to a TAK Server', true);
    actions.append(bInvite, bJoin, bTak);
    const status = el('div', 'min-height:1.2em;margin:6px 0;font-weight:600');
    const panel = el('div', 'margin:8px 0;padding:10px;border:1px solid rgba(255,255,255,0.12);border-radius:8px;display:none');
    const list = el('div');
    body.append(actions, status, panel, el('div', 'font-weight:600;margin:14px 0 6px', 'Linked servers'), list);

    function show(nodes) { panel.innerHTML = ''; panel.append(...nodes); panel.style.display = 'block'; }
    let me = {};

    // What the other server's admin does (an official TAK Server's side cannot be automated).
    function adminSheet(tak) {
      const box = el('div', small);
      const ours = el('a', 'display:inline-block;margin:6px 0;font-weight:600', 'Download this server\'s CA certificate');
      ours.href = '/api/federation/our-ca';
      const steps = el('ol', 'margin:6px 0 0 18px;padding:0');
      [
        'Send them the CA certificate above (a small .pem file).',
        tak ? 'TAK Server admin page > Federation > Manage Federate Certificate Authorities > upload it.'
            : 'They add it to their server\'s federation truststore.',
        tak ? 'Federation must be on, with v2 on port 9001 (the TAK Server default).' : 'Their federation port (9001) must be reachable.',
        'When this server connects it appears as "' + (me.our_common_name || 'PortableTAK') + '" in their federates: ' +
          'they set its inbound and outbound groups (users without a group are __ANON__).',
        tak ? 'TAK Server users who have a certificate but no group only federate if "x509addAnonymous" is on, or once they are in a shared group.' : ''
      ].filter(Boolean).forEach(t => steps.appendChild(el('li', 'margin:3px 0', t)));
      box.append(el('div', 'font-weight:600', 'For the other server\'s admin'), ours, steps,
        el('div', 'font-family:monospace;font-size:0.8em;word-break:break-all;margin-top:6px',
          'This server\'s federation fingerprint: ' + (me.our_fingerprint ? pretty(me.our_fingerprint) : '(shown once federation is on)')));
      return box;
    }

    bInvite.onclick = async () => {
      status.textContent = ''; show([el('div', small, 'Making an invite...')]);
      const r = await call('/invite', 'POST');
      if (!r.ok) { show([el('div', '', msg(why(r.data)))]); return; }
      const code = el('textarea', 'width:100%;height:80px;font-family:monospace;font-size:0.85em');
      code.readOnly = true; code.value = r.data.code;
      const copy = btn('Copy code');
      copy.onclick = async () => { try { await navigator.clipboard.writeText(r.data.code); copy.textContent = 'Copied'; } catch (e) { code.select(); } };
      const save = btn('Download invite file');
      save.onclick = () => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([r.data.code + '\n'], {type: 'text/plain'}));
        a.download = 'Invite to ' + r.data.name + '.txt'; a.click();
      };
      show([el('div', 'font-weight:600', 'Invite another PortableTAK'),
            el('div', small, 'Send this code (or the file) to the other server\'s admin. On their dashboard: Federation > Join a server > paste it. ' +
              'It works once, for 24 hours. Both servers need a network path to each other (same network, or the same Tailscale account).'),
            code, copy, save]);
      refresh();
    };

    bJoin.onclick = () => {
      status.textContent = '';
      const code = el('textarea', 'width:100%;height:80px;font-family:monospace;font-size:0.85em');
      code.placeholder = 'Paste the invite code (PTFED1.…)';
      const file = el('input'); file.type = 'file'; file.accept = '.txt';
      file.onchange = async () => { if (file.files[0]) code.value = (await file.files[0].text()).trim(); };
      const dir = directionSelect('both');
      const go = btn('Join', true);
      go.onclick = async () => {
        go.disabled = true; status.textContent = 'Joining (about 30 seconds)...';
        const r = await call('/join', 'POST', {code: code.value, direction: dir.value});
        go.disabled = false;
        status.textContent = r.ok ? 'Joined ' + r.data.name + '. The link turns green within a minute.' : msg(why(r.data));
        if (r.ok) { panel.style.display = 'none'; refresh(); }
      };
      show([el('div', 'font-weight:600', 'Join a server with an invite'),
            el('div', small, 'Paste the code from the other server\'s admin, or open the invite file:'), code, file,
            el('div', small, 'Sharing (you can change this later):'), dir, el('div'), go]);
    };

    bTak.onclick = () => {
      status.textContent = '';
      const name = el('input', 'width:100%;margin:3px 0'); name.placeholder = 'Name, e.g. HQ TAK Server';
      const address = el('input', 'width:100%;margin:3px 0'); address.placeholder = 'Address, e.g. tak.example.org or 100.x.y.z';
      const port = el('input', 'width:100%;margin:3px 0'); port.placeholder = 'Federation port (9001)'; port.inputMode = 'numeric';
      const dir = directionSelect('both');
      const check = btn('Check', true);
      const result = el('div', 'margin-top:8px');
      check.onclick = async () => {
        check.disabled = true; result.textContent = 'Contacting the server...';
        const p = port.value.trim() ? Number(port.value) : 9001;
        const r = await call('/tak/check', 'POST', {address: address.value.trim(), port: p});
        check.disabled = false;
        if (!r.ok) { result.textContent = msg(why(r.data)); return; }
        const d = r.data;
        const trust = btn('Trust and connect', true);
        result.innerHTML = '';
        result.append(el('div', small, 'The server identifies as: ' + d.server_subject),
          el('div', small, 'Its certificate authority: ' + d.ca_subject),
          el('div', 'font-family:monospace;font-size:0.8em;word-break:break-all;margin:4px 0', 'CA fingerprint: ' + pretty(d.ca_fingerprint)),
          el('div', small, 'If you can, have the TAK Server admin confirm this fingerprint. Then:'), trust);
        trust.onclick = async () => {
          trust.disabled = true; status.textContent = 'Connecting...';
          const c = await call('/tak/connect', 'POST', {name: name.value.trim() || 'TAK Server', address: address.value.trim(), port: p,
            ca: d.ca, server_fingerprint: d.server_fingerprint, direction: dir.value});
          trust.disabled = false;
          if (!c.ok) { status.textContent = msg(why(c.data)); return; }
          await refresh();
          status.textContent = 'Added on this side. Now the TAK Server admin finishes theirs:';
          show([adminSheet(true)]);
        };
      };
      show([el('div', 'font-weight:600', 'Connect to an official TAK Server'),
            el('div', small, 'PortableTAK reads the TAK Server\'s certificate itself; no files needed from its admin.'),
            name, address, port, el('div', small, 'Sharing (you can change this later):'), dir, el('div'), check, result]);
    };

    // ---- linked servers --------------------------------------------------------
    const pending = f => !f.outbound && !(f.inbound_groups || []).length && !(f.outbound_groups || []).length;
    const linkState = f => {
      if (!f.enabled) return ['#6e7681', 'Off'];
      if (pending(f)) return ['#d29922', 'Connected to this server, not sharing yet'];
      const up = f.last_connected && (!f.last_disconnected || f.last_connected > f.last_disconnected);
      if (up) return ['#3fb950', 'Connected'];
      return f.last_error ? ['#f85149', 'Not connected: ' + f.last_error] : ['#d29922', 'Waiting to connect'];
    };

    // One linked server: [this server] <- -> [other server]; tap an arrow to change
    // what flows that way (green = on). Pause/Resume and Delete on the right.
    function save(f, changes) {
      return call('/federates', 'POST', Object.assign({name: f.name, address: f.address || '', port: f.port,
        protocol_version: f.protocol_version, outbound: f.outbound, enabled: f.enabled,
        inbound_groups: (f.inbound_groups || []).length ? f.inbound_groups : [],
        outbound_groups: (f.outbound_groups || []).length ? f.outbound_groups : []}, changes));
    }
    function badge(text, color) {
      return el('span', 'display:inline-block;margin-top:4px;padding:1px 8px;border-radius:6px;font-size:0.8em;font-weight:600;' +
        'color:white;background:' + color, text);
    }
    function linkRow(f, ours) {
      const row = el('div', 'display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:12px;margin:8px 0;' +
        'border-radius:10px;background:rgba(255,255,255,0.04)');
      const [color, text] = linkState(f);
      const receiving = (f.inbound_groups || []).length > 0, sending = (f.outbound_groups || []).length > 0;
      const side = (name, sub) => { const b = el('div', 'text-align:center;min-width:120px'); b.append(el('div', 'font-weight:700', name), sub); return b; };
      const left = side(ours, badge(d0.process === 'RUNNING' ? 'this server' : 'federation off', d0.process === 'RUNNING' ? '#2d6cdf' : '#6e7681'));
      const right = side(f.name, badge(f.enabled ? text.split(':')[0] : 'paused', color));
      const middle = el('div', 'text-align:center;flex:1;min-width:200px');
      const arrows = el('div', 'font-size:1.8em;line-height:1;user-select:none');
      const arrow = (glyph, on, label) => {
        const a = el('span', 'cursor:pointer;padding:0 8px;color:' + (on ? '#3fb950' : '#8b949e'), glyph);
        a.title = label; return a;
      };
      const inArrow = arrow('\u2190', receiving, 'Receive from ' + f.name);
      const outArrow = arrow('\u2192', sending, 'Send to ' + f.name);
      arrows.append(inArrow, outArrow);
      const flow = receiving && sending ? 'Data flows both ways.' :
        receiving ? "Data flows into '" + ours + "' from '" + f.name + "'." :
        sending ? "Data flows from '" + ours + "' into '" + f.name + "'." : 'Not sharing yet.';
      middle.append(arrows, el('div', small, pending(f) ? 'This server connected to yours.' : 'Tap an arrow to change data flow.'),
        el('div', 'margin-top:4px;font-weight:600', flow));
      if (f.last_error && f.enabled && !text.startsWith('Connected')) middle.append(el('div', small + ';color:#f0b429', text));
      const toggle = async (which) => {
        const nextIn = which === 'in' ? !receiving : receiving, nextOut = which === 'out' ? !sending : sending;
        if (!nextIn && !nextOut) { status.textContent = 'At least one direction stays on; use Pause to stop sharing.'; return; }
        const r = await save(f, {inbound_groups: nextIn ? ((f.inbound_groups || []).length ? f.inbound_groups : 'everyone') : [],
                                 outbound_groups: nextOut ? ((f.outbound_groups || []).length ? f.outbound_groups : 'everyone') : []});
        status.textContent = r.ok ? '' : msg(why(r.data)); refresh();
      };
      inArrow.onclick = () => toggle('in');
      outArrow.onclick = () => toggle('out');
      const buttons = el('div', 'display:flex;flex-direction:column;gap:6px;align-items:stretch');
      if (pending(f)) {
        const allow = btn('Allow sharing', true);
        allow.onclick = async () => { await save(f, {inbound_groups: 'everyone', outbound_groups: 'everyone', enabled: true}); refresh(); };
        buttons.append(allow);
      } else {
        const pause = btn(f.enabled ? 'Pause' : 'Resume');
        pause.onclick = async () => { await save(f, {enabled: !f.enabled}); refresh(); };
        buttons.append(pause);
      }
      const del = btn('Delete'); del.className = 'danger';
      del.onclick = async () => { if (confirm('Stop sharing with ' + f.name + ' and remove it?')) { await call('/federates/' + encodeURIComponent(f.name), 'DELETE'); refresh(); } };
      const reset = el('a', 'font-size:0.8em;opacity:0.7;cursor:pointer;text-align:center', 'Forget its certificate');
      reset.title = 'Use only if the other server got a new certificate';
      reset.onclick = async () => { await save(f, {reset_pin: true}); refresh(); };
      const chLink = el('a', 'font-size:0.8em;opacity:0.85;cursor:pointer;text-align:center', 'Channels...');
      chLink.title = 'Which channels this link shares';
      buttons.append(del, chLink, reset);
      row.append(left, middle, right, buttons);
      const panel = el('div', 'flex-basis:100%;display:none;margin-top:6px;padding:8px;border-radius:8px;background:rgba(255,255,255,0.04)');
      chLink.onclick = () => { panel.style.display = panel.style.display === 'none' ? 'block' : 'none'; };
      const grid = el('div', 'display:grid;grid-template-columns:1fr auto auto;gap:6px 16px;align-items:center;max-width:480px');
      grid.append(el('span', small, 'Channel'), el('span', small, 'Send to ' + f.name), el('span', small, 'Receive into'));
      const outSet = new Set(f.outbound_groups || []), inSet = new Set(f.inbound_groups || []);
      const names = Array.from(new Set([...channelNames, ...outSet, ...inSet]));
      const rows = names.map(n => {
        const o = el('input'); o.type = 'checkbox'; o.checked = outSet.has(n);
        const i = el('input'); i.type = 'checkbox'; i.checked = inSet.has(n);
        grid.append(el('span', '', n), o, i);
        return [n, o, i];
      });
      const saveCh = btn('Save channels', true);
      saveCh.onclick = async () => {
        const out = rows.filter(r => r[1].checked).map(r => r[0]), inn = rows.filter(r => r[2].checked).map(r => r[0]);
        if (!out.length && !inn.length) { status.textContent = 'Tick at least one channel, or use Pause.'; return; }
        const r = await save(f, {outbound_groups: out, inbound_groups: inn});
        status.textContent = r.ok ? 'Channels for ' + f.name + ' saved.' : msg(why(r.data)); refresh();
      };
      panel.append(el('div', small, 'Send: data from users in these channels goes to ' + f.name + '. Receive: ' + f.name +
        '\'s data arrives in these channels here. Official TAK Servers match channels by name.'), grid, saveCh);
      row.append(panel);
      if (f.outbound) row.append(el('div', 'flex-basis:100%;' + small + ';text-align:center', 'Connects to ' + f.address + ':' + f.port));
      return row;
    }
    let d0 = {};
    let channelNames = ['__ANON__'];

    // ---- advanced ---------------------------------------------------------------
    const adv = el('details', 'margin-top:14px;' + small);
    adv.append(el('summary', 'cursor:pointer;font-weight:600', 'Advanced'));
    const advBody = el('div', 'margin-top:8px');
    adv.appendChild(advBody);
    body.appendChild(adv);
    const top = el('div', 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px');
    const state = el('span', 'font-weight:600'); const toggle = el('button', '', '...');
    top.append(state, toggle);
    const file = el('input'); file.type = 'file'; file.accept = '.pem,.crt,.cer';
    const cas = el('div', 'margin:6px 0');
    const form = el('div', 'display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px');
    const fName = el('input'); fName.placeholder = 'Name';
    const fAddr = el('input'); fAddr.placeholder = 'Address';
    const fProto = el('select');
    [['2', 'v2 (port 9001)'], ['1', 'v1 (port 9000)']].forEach(([v, t]) => { const o = el('option', '', t); o.value = v; fProto.appendChild(o); });
    const fPort = el('input'); fPort.placeholder = 'Port'; fPort.inputMode = 'numeric';
    const fDial = el('label', 'grid-column:1 / -1'); const dial = el('input'); dial.type = 'checkbox'; dial.checked = true;
    fDial.append(dial, document.createTextNode(' This server connects to it'));
    const gIn = el('input'); gIn.placeholder = 'Groups received (blank = everyone)';
    const gOut = el('input'); gOut.placeholder = 'Groups sent (blank = everyone)';
    const fSave = el('button', 'grid-column:1 / -1', 'Add server manually');
    form.append(fName, fAddr, fProto, fPort, fDial, gIn, gOut, fSave);
    const sheetSlot = el('div', 'margin-top:10px');
    advBody.append(top, el('div', '', 'Trusted certificate authorities (upload a .pem):'), file, cas, form, sheetSlot);

    const groupList = v => v.trim() ? v.split(',').map(g => g.trim()).filter(Boolean) : 'everyone';
    file.onchange = async () => {
      const f = file.files[0]; if (!f) return;
      const r = await call('/ca', 'POST', {pem: await f.text(), filename: f.name});
      status.textContent = r.ok ? 'Certificate added.' : msg(why(r.data)); file.value = ''; refresh();
    };
    fSave.onclick = async () => {
      const r = await call('/federates', 'POST', {name: fName.value.trim(), address: fAddr.value.trim(), protocol_version: Number(fProto.value),
        port: fPort.value.trim() ? Number(fPort.value) : undefined, outbound: dial.checked, enabled: true,
        inbound_groups: groupList(gIn.value), outbound_groups: groupList(gOut.value)});
      status.textContent = r.ok ? 'Saved.' : msg(why(r.data)); refresh();
    };

    async function refresh() {
      const r = await call('');
      const d = r.data || {};
      if (!r.ok) { status.textContent = msg(why(d)); return; }
      me = d; d0 = d;
      const ch = await fetch('/api/channels').then(x => x.json()).catch(() => ({}));
      channelNames = ((ch && ch.channels) || []).map(c => c.name);
      if (!channelNames.length) channelNames = ['__ANON__'];
      state.textContent = d.enabled ? 'Federation is ON' : 'Federation is OFF (it turns on by itself when you invite, join or connect)';
      toggle.textContent = d.enabled ? 'Turn off' : 'Turn on';
      toggle.onclick = async () => { await call('/enable', 'POST', {enabled: !d.enabled}); refresh(); };
      sheetSlot.innerHTML = ''; sheetSlot.appendChild(adminSheet(false));
      cas.innerHTML = '';
      (d.cas || []).forEach(c => {
        const line = el('div', 'display:flex;gap:8px;align-items:center;padding:2px 0');
        const del = el('button', '', 'Remove');
        del.onclick = async () => { if (confirm('Stop trusting ' + c.filename + '?')) { await call('/ca/' + encodeURIComponent(c.filename), 'DELETE'); refresh(); } };
        line.append(el('span', 'flex:1', c.subject || c.filename), del);
        cas.appendChild(line);
      });
      list.innerHTML = '';
      if (!(d.federates || []).length) list.appendChild(el('div', 'opacity:0.75', 'None yet. Use the buttons above.'));
      (d.federates || []).forEach(f => list.appendChild(linkRow(f, d.name || 'This server')));
    }

    details.addEventListener('toggle', () => { if (details.open) refresh(); });
    setInterval(() => { if (details.open) refresh(); }, 15000);

    // Directly after Remote Access (or Users).
    const headings = Array.from(document.querySelectorAll('section > h2, section summary'));
    const heading = headings.find(h => h.textContent.trim() === 'Remote Access') || headings.find(h => h.textContent.trim() === 'Users');
    const after = heading && heading.closest('section');
    if (after) after.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  const go = () => setTimeout(build, 5);   // after the Remote Access card
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();

// "Channels" card: TAK channels (OpenTAKServer groups). Add, rename and delete channels,
// and choose which channels each user sends into and receives from.
(function () {
  const call = (path, method, body) => fetch(path, {
    method: method || 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const small = 'font-size:0.9em;opacity:0.85;line-height:1.5';
  const why = d => (d && d.detail && (d.detail.error || d.detail)) || (d && d.error) || 'unknown error';
  const ERRORS = {
    invalid_channel_name: 'Use letters, numbers, spaces, dots, dashes or underscores (up to 64).',
    channel_exists: 'There is already a channel with that name.',
    builtin_channel: 'Built-in channels (__ANON__, ADS-B, AIS, Meshtastic) cannot be renamed or deleted.',
    ldap_enabled: 'Channels come from the LDAP server on this setup.',
    user_not_found: 'That user no longer exists.'
  };
  const msg = e => ERRORS[e] || String(e);
  const btn = (text, primary) => { const b = el('button', 'margin:2px 6px 2px 0', text); if (primary) b.className = 'primary'; return b; };

  function build() {
    const card = el('section', 'margin-top:18px'); card.className = 'card';
    const details = el('details');
    details.append(el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Channels'));
    const body = el('div', 'margin-top:14px'); details.appendChild(body); card.appendChild(details);
    body.append(el('div', 'margin:0 0 12px;' + small,
      'Channels split who sees what. A user can send into a channel, receive from it, or both. Users in no channel use ' +
      '__ANON__ (everyone without a channel). Phones pick up changes when they reconnect; in ATAK and iTAK users can switch their channels on and off.'));
    const status = el('div', 'min-height:1.2em;margin:4px 0;font-weight:600');
    const list = el('div');
    const addRow = el('div', 'display:flex;gap:8px;flex-wrap:wrap;margin:10px 0');
    const newName = el('input', 'flex:1;min-width:160px'); newName.placeholder = 'New channel, e.g. Team 1';
    const add = btn('Add channel', true);
    addRow.append(newName, add);

    const who = el('div', 'margin-top:16px');
    const pickRow = el('div', 'display:flex;gap:8px;align-items:center;flex-wrap:wrap');
    const userSel = el('select', 'min-width:160px');
    pickRow.append(el('span', 'font-weight:600', 'Channels for user:'), userSel);
    const table = el('div', 'margin-top:8px');
    const save = btn('Save', true); save.style.display = 'none';
    who.append(pickRow, table, save);
    body.append(status, list, addRow, who);

    let channels = [];

    async function refresh() {
      const r = await call('/api/channels');
      if (!r.ok) { status.textContent = msg(why(r.data)); return; }
      channels = r.data.channels || [];
      list.innerHTML = '';
      channels.forEach(c => {
        const line = el('div', 'display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.08);flex-wrap:wrap');
        line.append(el('span', 'font-weight:600;min-width:140px', c.name),
          el('span', 'flex:1;' + small, c.builtin ? 'Built-in: ' + (c.builtin_note || 'used by the server') :
            (c.members + (c.members === 1 ? ' user' : ' users') + ' (' + c.send + ' send, ' + c.receive + ' receive)')));
        if (!c.builtin) {
          const rename = btn('Rename');
          rename.onclick = async () => {
            const name = prompt('New name for ' + c.name + ':', c.name);
            if (!name || name === c.name) return;
            const x = await call('/api/channels/rename', 'POST', {name: c.name, new_name: name.trim()});
            status.textContent = x.ok ? 'Renamed. Phones see the new name when they reconnect.' : msg(why(x.data));
            refresh(); loadUser();
          };
          const del = btn('Delete'); del.className = 'danger';
          del.onclick = async () => {
            if (!confirm('Delete channel ' + c.name + '? Its users move back to __ANON__ unless they are in another channel.')) return;
            const x = await call('/api/channels/' + encodeURIComponent(c.name), 'DELETE');
            status.textContent = x.ok ? 'Deleted.' : msg(why(x.data));
            refresh(); loadUser();
          };
          line.append(rename, del);
        }
        list.appendChild(line);
      });
      const u = await call('/api/users');
      const current = userSel.value;
      userSel.innerHTML = '';
      const none = el('option', '', 'choose a user'); none.value = ''; userSel.appendChild(none);
      ((u.data && u.data.users) || []).forEach(x => { const o = el('option', '', x.username); o.value = x.username; userSel.appendChild(o); });
      userSel.value = current;
      loadUser();
    }

    async function loadUser() {
      table.innerHTML = ''; save.style.display = 'none';
      if (!userSel.value) return;
      const r = await call('/api/users/' + encodeURIComponent(userSel.value) + '/channels');
      if (!r.ok) { table.textContent = msg(why(r.data)); return; }
      const mine = {};
      (r.data.channels || []).forEach(c => { mine[c.name] = c; });
      const grid = el('div', 'display:grid;grid-template-columns:1fr auto auto;gap:6px 18px;align-items:center;max-width:420px');
      grid.append(el('span', small, 'Channel'), el('span', small, 'Send'), el('span', small, 'Receive'));
      const boxes = [];
      channels.forEach(c => {
        const s = el('input'); s.type = 'checkbox'; s.checked = !!(mine[c.name] && mine[c.name].send);
        const v = el('input'); v.type = 'checkbox'; v.checked = !!(mine[c.name] && mine[c.name].receive);
        boxes.push([c.name, s, v]);
        grid.append(el('span', '', c.name), s, v);
      });
      table.append(grid, el('div', small + ';margin-top:6px',
        'No channel ticked = the user is in __ANON__. Ticking __ANON__ as well keeps them seeing everyone without a channel.'));
      save.style.display = 'inline-block';
      save.onclick = async () => {
        const x = await call('/api/users/' + encodeURIComponent(userSel.value) + '/channels', 'POST',
          {channels: boxes.filter(([, s, v]) => s.checked || v.checked).map(([name, s, v]) => ({name, send: s.checked, receive: v.checked}))});
        status.textContent = x.ok ? 'Saved for ' + userSel.value + '. Their phone picks it up when it reconnects.' : msg(why(x.data));
        refresh();
      };
    }

    add.onclick = async () => {
      const name = newName.value.trim(); if (!name) return;
      const r = await call('/api/channels', 'POST', {name});
      status.textContent = r.ok ? 'Channel ' + name + ' added. Now choose its users below.' : msg(why(r.data));
      if (r.ok) newName.value = '';
      refresh();
    };
    userSel.onchange = loadUser;
    details.addEventListener('toggle', () => { if (details.open) refresh(); });

    // Directly after Users.
    const headings = Array.from(document.querySelectorAll('section > h2, section summary'));
    const users = headings.find(h => h.textContent.trim() === 'Users');
    const after = users && users.closest('section');
    if (after) after.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();

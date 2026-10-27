// "Data Packages" card: the server's data packages (the ones phones send and share, and
// ones uploaded here). Upload a data package or any file (wrapped in one), send it to
// phones (ATAK shows its usual "file received" notice and downloads it from this
// server), download or delete it.
(function () {
  const call = (path, method, body) => fetch(path, {
    method: method || 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const small = 'font-size:0.9em;opacity:0.85;line-height:1.5';
  const ERRORS = {file_too_large: 'The file is too big (256 MB at most).', empty_file: 'That file is empty.',
    not_found: 'That package is no longer on the server.', choose_phones: 'Choose at least one phone.'};
  const why = d => (d && d.detail && (d.detail.error || d.detail)) || (d && d.error) || 'unknown error';
  const msg = e => ERRORS[e] || String(e);
  const size = n => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
  const when = t => { try { return new Date(t).toLocaleString(); } catch (e) { return ''; } };

  function build() {
    const card = el('section', 'margin-top:18px'); card.className = 'card';
    const details = el('details');
    details.append(el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Data Packages'));
    const body = el('div', 'margin-top:14px'); details.appendChild(body); card.appendChild(details);
    body.append(el('div', 'margin:0 0 10px;' + small,
      'Files for phones: maps, overlays (KML/KMZ), documents, images or ready-made ATAK data packages. Packages phones ' +
      'send or share also show here. Send one to phones: ATAK shows "file received" and downloads it from this server.'));
    const status = el('div', 'min-height:1.2em;margin:4px 0;font-weight:600');
    const upRow = el('div', 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0 12px');
    const file = el('input'); file.type = 'file'; file.style.cssText = 'flex:1;min-width:200px';
    const upload = el('button', '', 'Upload'); upload.className = 'primary';
    upRow.append(file, upload);
    const list = el('div');
    body.append(upRow, status, list);
    let phones = [];

    function sendPanel(pkg, holder) {
      holder.innerHTML = '';
      const box = el('div', 'margin:6px 0 10px;padding:10px;border:1px solid rgba(255,255,255,0.12);border-radius:10px');
      const online = phones.filter(p => p.online), offline = phones.filter(p => !p.online);
      if (!phones.length) { box.append(el('div', small, 'No phones have connected to this server yet.')); holder.appendChild(box); return; }
      box.append(el('div', 'font-weight:600;margin-bottom:6px', 'Send "' + pkg.filename + '" to:'));
      const boxes = [];
      online.concat(offline).forEach(p => {
        const label = el('label', 'display:flex;align-items:center;gap:8px;padding:3px 0' + (p.online ? '' : ';opacity:0.55'));
        const cb = el('input', 'width:auto;min-width:0;margin:0'); cb.type = 'checkbox'; cb.disabled = !p.online; cb.value = p.uid;
        label.append(cb, document.createTextNode(p.callsign + (p.user ? ' (' + p.user + ')' : '') + (p.online ? '' : ' - not connected')));
        boxes.push(cb); box.appendChild(label);
      });
      if (offline.length) box.append(el('div', small, 'Only connected phones can receive it; send again later for the others.'));
      const go = el('button', 'margin-top:8px', 'Send'); go.className = 'primary';
      const cancel = el('button', 'margin:8px 0 0 8px', 'Cancel');
      cancel.onclick = () => { holder.innerHTML = ''; };
      go.onclick = async () => {
        const uids = boxes.filter(b => b.checked).map(b => b.value);
        if (!uids.length) { status.textContent = msg('choose_phones'); return; }
        go.disabled = true;
        const r = await call('/api/datapackages/send', 'POST', {hash: pkg.hash, uids});
        go.disabled = false;
        status.textContent = r.ok && r.data.ok ? 'Sent to ' + r.data.sent.map(p => p.callsign).join(', ') + '.' : msg(why(r.data));
        if (r.ok && r.data.ok) holder.innerHTML = '';
      };
      box.append(go, cancel); holder.appendChild(box);
    }

    async function refresh() {
      const r = await call('/api/datapackages');
      if (!r.ok || !r.data.ok) { status.textContent = msg(why(r.data)); return; }
      phones = r.data.phones || [];
      list.innerHTML = '';
      const pkgs = r.data.packages || [];
      if (!pkgs.length) list.appendChild(el('div', 'opacity:0.75', 'No data packages yet.'));
      pkgs.forEach(p => {
        const wrap = el('div', 'border-bottom:1px solid rgba(255,255,255,0.08);padding:6px 0');
        const line = el('div', 'display:flex;align-items:center;gap:10px;flex-wrap:wrap');
        const info = el('div', 'flex:1;min-width:180px');
        info.append(el('div', 'font-weight:600;word-break:break-all', p.filename),
          el('div', 'font-size:0.85em;opacity:0.75', [size(p.size), p.from ? 'from ' + p.from : '', when(p.time)].filter(Boolean).join(' · ')));
        const holder = el('div');
        const send = el('button', '', 'Send'); send.className = 'primary';
        send.onclick = () => (holder.firstChild ? (holder.innerHTML = '') : sendPanel(p, holder));
        const get = el('a', 'padding:6px 12px;border:1px solid var(--border,#293748);border-radius:8px;color:var(--text,#e8eef5);text-decoration:none;font-size:0.95em', 'Download');
        get.href = '/api/datapackages/' + p.hash + '/download';
        const del = el('button', '', 'Delete'); del.className = 'danger';
        del.onclick = async () => {
          if (!confirm('Delete "' + p.filename + '" from the server? Phones that already have it keep their copy.')) return;
          const x = await call('/api/datapackages/' + p.hash, 'DELETE');
          status.textContent = x.ok && x.data.ok ? 'Deleted.' : msg(why(x.data));
          refresh();
        };
        [send, del].forEach(b => { b.style.setProperty('width', 'auto', 'important'); b.style.padding = '6px 12px'; });
        line.append(info, send, get, del);
        wrap.append(line, holder);
        list.appendChild(wrap);
      });
    }

    upload.onclick = async () => {
      const f = file.files && file.files[0];
      if (!f) { status.textContent = 'Choose a file first.'; return; }
      if (f.size > 256 * 1024 * 1024) { status.textContent = msg('file_too_large'); return; }
      upload.disabled = true; status.textContent = 'Uploading ' + f.name + '…';
      const r = await fetch('/api/datapackages/upload?filename=' + encodeURIComponent(f.name), {method: 'POST', body: f})
        .then(async x => ({ok: x.ok, data: await x.json().catch(() => ({}))}));
      upload.disabled = false;
      if (r.ok && r.data.ok) {
        status.textContent = r.data.existing ? 'That package is already on the server.' : 'Uploaded ' + r.data.package.filename + '.';
        file.value = '';
      } else status.textContent = msg(why(r.data));
      refresh();
    };
    details.addEventListener('toggle', () => { if (details.open) refresh(); });

    const heads = Array.from(document.querySelectorAll('section summary'));
    const anchor = heads.find(h => h.textContent.trim() === 'ATAK Plugins');
    const after = anchor && anchor.closest('section');
    if (after) after.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  const go = () => setTimeout(build, 30);   // after ATAK Plugins (the fixed order is set by desktop-layout.js)
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();

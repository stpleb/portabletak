// "ATAK Plugins" card: the TAK update server. Upload ATAK plugins / apps (APK); phones set up
// from this server see them in ATAK's plugin list and can install or update them, and can
// get them automatically when they enroll or connect.
(function () {
  const call = (path, method, body, raw) => fetch(path, {
    method: method || 'GET',
    headers: raw ? {'Content-Type': 'application/vnd.android.package-archive'} : (body ? {'Content-Type': 'application/json'} : {}),
    body: raw || (body ? JSON.stringify(body) : undefined)
  }).then(async r => ({ok: r.ok, status: r.status, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const small = 'font-size:0.9em;opacity:0.85;line-height:1.5';
  const ERRORS = { not_an_android_app: 'That file is not an Android app (APK).', apk_too_large: 'The file is too big (256 MB at most).',
    upload_missing: 'The upload did not arrive. Try again.', not_found: 'That plugin is no longer on the server.' };
  const why = d => (d && d.detail && (d.detail.error || d.detail)) || (d && d.error) || 'unknown error';
  const msg = (e, status) => status === 413 ? ERRORS.apk_too_large : (ERRORS[e] || String(e));
  const mb = b => (b / 1048576).toFixed(b > 10485760 ? 0 : 1) + ' MB';

  function build() {
    const card = el('section', 'margin-top:18px'); card.className = 'card';
    const details = el('details');
    details.append(el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'ATAK Plugins'));
    const body = el('div', 'margin-top:14px'); details.appendChild(body); card.appendChild(details);
    body.append(el('div', 'margin:0 0 10px;' + small,
      'ATAK plugins and apps phones can install from this server, without internet. Phones set up with ' +
      'this server’s QR code or package find them in ATAK under Plugins (tap ⋮ then Sync). ' +
      '"On setup" installs a plugin when a phone enrolls; "On connect" offers it every time a phone connects.'));
    const status = el('div', 'min-height:1.2em;margin:4px 0;font-weight:600');
    const list = el('div');
    const pick = el('input'); pick.type = 'file'; pick.accept = '.apk,application/vnd.android.package-archive'; pick.style.display = 'none';
    const upload = el('button', 'margin-top:10px', 'Upload plugin (APK)'); upload.className = 'primary';
    const setup = el('label', 'display:inline-flex;gap:6px;align-items:center;margin:10px 0 0 10px;' + small);
    const setupBox = el('input', 'width:auto;margin:0'); setupBox.type = 'checkbox';
    setup.append(setupBox, 'Install on setup');
    body.append(status, list, upload, setup, pick);

    async function refresh() {
      const r = await call('/api/plugins');
      if (!r.ok) { status.textContent = msg(why(r.data), r.status); return; }
      const plugins = r.data.plugins || [];
      list.innerHTML = '';
      if (!plugins.length) list.append(el('div', small, 'No plugins yet.'));
      plugins.forEach(p => {
        const row = el('div', 'display:flex;gap:12px;align-items:center;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.08);flex-wrap:wrap');
        const icon = p.icon ? Object.assign(el('img', 'width:36px;height:36px;border-radius:8px'), {src: p.icon, alt: ''}) : el('div', 'width:36px;height:36px;border-radius:8px;background:#18222e');
        const info = el('div', 'flex:1;min-width:180px');
        info.append(el('div', 'font-weight:600', p.name), el('div', small,
          'Version ' + p.version + ' · ' + mb(p.file_size) + (p.tak_prereq ? ' · needs ' + p.tak_prereq.replace('com.atakmap.app@', 'ATAK ') : '')));
        if (p.description) info.append(el('div', small, p.description));
        const toggle = (label, key) => {
          const l = el('label', 'display:inline-flex;gap:5px;align-items:center;' + small);
          const b = el('input', 'width:auto;margin:0'); b.type = 'checkbox'; b.checked = !!p[key];
          b.onchange = async () => {
            const x = await call('/api/plugins/update', 'POST', {package_name: p.package_name, [key]: b.checked});
            status.textContent = x.ok ? 'Saved.' : msg(why(x.data), x.status);
          };
          l.append(b, label); return l;
        };
        const del = el('button', '', 'Delete'); del.className = 'danger';
        del.onclick = async () => {
          if (!confirm('Remove ' + p.name + ' from the server? Phones keep the copy they have.')) return;
          const x = await call('/api/plugins/' + encodeURIComponent(p.package_name), 'DELETE');
          status.textContent = x.ok ? 'Removed.' : msg(why(x.data), x.status);
          refresh();
        };
        row.append(icon, info, toggle('On setup', 'install_on_enrollment'), toggle('On connect', 'install_on_connection'), del);
        list.appendChild(row);
      });
    }

    upload.onclick = () => pick.click();
    pick.onchange = async () => {
      const file = pick.files[0]; pick.value = '';
      if (!file) return;
      status.textContent = 'Uploading ' + file.name + '…';
      const r = await call('/api/plugins/upload?install_on_enrollment=' + (setupBox.checked ? 'true' : 'false'), 'POST', null, file);
      status.textContent = r.ok && r.data.ok ? 'Added ' + r.data.plugin.name + ' ' + r.data.plugin.version + '.' : msg(why(r.data), r.status);
      refresh();
    };
    details.addEventListener('toggle', () => { if (details.open) refresh(); });

    // After Live Feeds / Channels; else at the end.
    const heads = Array.from(document.querySelectorAll('section summary'));
    const anchor = heads.find(h => h.textContent.trim() === 'Live Feeds') || heads.find(h => h.textContent.trim() === 'Channels');
    const after = anchor && anchor.closest('section');
    if (after) after.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  const go = () => setTimeout(build, 20);   // after the Channels and Live Feeds cards
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();

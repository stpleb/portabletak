// "Data Sync" card: the feeds on this server, which ATAK's Data Sync plugin shows. ATAK's
// trash cans only clear the phone's copy, so a feed is deleted from the server here.
(function () {
  const call = (path, method) => fetch(path, {method: method || 'GET'})
    .then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const small = 'font-size:0.9em;opacity:0.85;line-height:1.5';
  const ERRORS = {not_found: 'That feed is no longer on the server.'};
  const why = d => (d && d.detail && (d.detail.error || d.detail)) || (d && d.error) || 'unknown error';
  const msg = e => ERRORS[e] || String(e);
  const when = t => { try { return new Date(t).toLocaleString(); } catch (e) { return ''; } };
  const count = (n, one) => n + ' ' + one + (n === 1 ? '' : 's');

  function build() {
    const card = el('section'); card.className = 'card'; card.style.marginTop = '18px';
    const details = el('details');
    details.append(el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Data Sync'));
    const body = el('div', 'margin-top:10px');
    body.appendChild(el('p', small,
      'Feeds on this server, as phones see them in ATAK\'s Data Sync. Deleting a feed removes it from the server ' +
      'for everyone. Files placed on a feed stay on the server. A phone offline at the time may keep its copy until it clears it.'));
    const status = el('div', 'min-height:1.4em;' + small);
    const list = el('div');
    body.append(list, status);
    details.appendChild(body);
    card.appendChild(details);

    async function refresh() {
      const r = await call('/api/datasync/feeds');
      if (!r.ok || !r.data.ok) { status.textContent = msg(why(r.data)); return; }
      list.innerHTML = '';
      const feeds = r.data.feeds || [];
      if (!feeds.length) list.appendChild(el('div', 'opacity:0.75', 'No Data Sync feeds on this server.'));
      feeds.forEach(f => {
        const line = el('div', 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-bottom:1px solid rgba(255,255,255,0.08);padding:6px 0');
        const info = el('div', 'flex:1;min-width:180px');
        info.append(el('div', 'font-weight:600;word-break:break-all', f.name),
          el('div', 'font-size:0.85em;opacity:0.75', [f.creator ? 'by ' + f.creator : '', count(f.items, 'item'),
            count(f.subscribers, 'subscriber'), f.password ? 'password' : '', f.time ? when(f.time) : ''].filter(Boolean).join(' · ')));
        const del = el('button', '', 'Delete'); del.className = 'danger';
        del.style.setProperty('width', 'auto', 'important'); del.style.padding = '6px 12px';
        del.onclick = async () => {
          if (!confirm('Delete the feed "' + f.name + '" from the server for everyone? This cannot be undone.')) return;
          const x = await call('/api/datasync/feeds?name=' + encodeURIComponent(f.name), 'DELETE');
          status.textContent = !x.ok || !x.data.ok ? msg(why(x.data))
            : x.data.phones_told ? 'Deleted.' : 'Deleted. Phones were not notified, so they may need to clear their copy.';
          refresh();
        };
        line.append(info, del);
        list.appendChild(line);
      });
    }
    details.addEventListener('toggle', () => { if (details.open) refresh(); });

    const heads = Array.from(document.querySelectorAll('section summary'));
    const anchor = heads.find(h => h.textContent.trim() === 'Data Packages');
    const after = anchor && anchor.closest('section');
    if (after) after.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  const go = () => setTimeout(build, 60);   // after Data Packages (the fixed order is set by desktop-layout.js)
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();

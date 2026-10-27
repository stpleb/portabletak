// Section order and desktop layout. The cards are built by separate scripts in whatever
// order they finish; one pass puts them in a fixed order (phones and desktop alike).
// Desktop layout: on wide screens the dashboard becomes a sidebar of pages (like
// OpenTAKServer's web UI) and shows one page at a time; phones and narrow windows keep
// the single scrolling column. Sections added later by the other scripts become pages
// as they appear.
(function () {
  const WIDE = window.matchMedia('(min-width: 1100px)');
  const DASHBOARD = 'dashboard', MAP = 'live-map';
  const store = {
    get() { try { return localStorage.getItem('pt-page'); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem('pt-page', v); } catch (e) { /* private mode */ } },
  };
  let nav = null, title = null, mapPage = null, current = null, opened = new Set(), observer = null, timer = null;

  const css = document.createElement('style');
  css.textContent = `
    body.pt-desk main { max-width: none; margin: 0 0 0 250px; padding: 22px 32px 32px; }
    body.pt-desk main > header .badge[href="/map"], body.pt-desk .pt-hide { display: none !important; }
    body.pt-desk .pt-page { margin-top: 0 !important; }
    body.pt-desk main > section.grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }   /* six cards: two even rows */
    body.pt-desk .pt-page > details > summary { display: none; }
    body.pt-desk .pt-page > details > div:first-of-type { margin-top: 0 !important; }
    #ptNav { position: fixed; top: 0; left: 0; bottom: 0; width: 250px; background: var(--panel, #121922);
      border-right: 1px solid var(--border, #293748); display: flex; flex-direction: column; z-index: 20; }
    #ptNav .pt-brand { padding: 18px 18px 14px; border-bottom: 1px solid var(--border, #293748); }
    #ptNav .pt-brand b { display: block; font-size: 1.05rem; color: #fff; }
    #ptNav .pt-brand span { font-size: 0.85rem; color: var(--muted, #8fa1b5); }
    #ptNav .pt-items { flex: 1; overflow-y: auto; padding: 8px; }
    #ptNav a { display: flex; align-items: center; gap: 10px; padding: 8px 10px; margin: 1px 0; border-radius: 8px;
      color: var(--text, #e8eef5); text-decoration: none; font-size: 0.95rem; cursor: pointer; }
    #ptNav a:hover { background: rgba(255,255,255,0.05); }
    #ptNav a.active { background: rgba(99,167,255,0.16); color: #fff; }
    #ptNav a svg { width: 18px; height: 18px; margin: 0 !important; flex: none; }
    #ptNav .pt-sep { height: 1px; background: var(--border, #293748); margin: 8px 6px; }
    #ptNav .pt-foot { padding: 8px; border-top: 1px solid var(--border, #293748); }
    #ptTitle { display: flex; align-items: center; gap: 10px; color: #fff; font-size: 1.5rem; font-weight: 600; margin: 4px 0 16px; }
    #ptTitle svg { width: 1.1em; height: 1.1em; margin: 0 !important; }
    #ptMap iframe { width: 100%; height: calc(100vh - 170px); min-height: 420px; border: 1px solid var(--border, #293748);
      border-radius: 12px; background: #0b0f14; }
  `;
  document.head.appendChild(css);

  const main = () => document.querySelector('main');
  const ORDER = ['Controls', 'Remote Access', 'Federation', 'Client Setup', 'Users', 'Channels', 'Live Feeds',
                 'Video Feeds', 'ATAK Plugins', 'Data Packages', 'Meshtastic', 'Backup / Restore', 'Activity'];

  function order() {
    const byName = {};
    Array.from(main().children).forEach(el => {
      const head = el.matches('section') && (el.querySelector(':scope > details > summary') || el.querySelector(':scope > h2'));
      if (head) byName[ownText(head)] = el;
    });
    let anchor = main().querySelector(':scope > section.grid');
    if (!anchor) return;
    ORDER.forEach(name => {
      const el = byName[name];
      if (!el) return;
      if (el.previousElementSibling !== anchor) anchor.after(el);   // only move what is out of place
      anchor = el;
    });
  }
  const slug = t => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const ownText = h => Array.from(h.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();

  // The pages: the status cards and controls form "Dashboard"; every other section is its own page.
  function pages() {
    const list = [{id: DASHBOARD, name: 'Dashboard', els: [], icon: null}];
    let after = false;
    Array.from(main().children).forEach(el => {
      if (el.tagName === 'HEADER' || el.id === 'ptTitle' || el.id === 'ptMap') return;
      if (el.classList.contains('grid') || el.id === 'controlsCard' || el.id === 'nodeActions' || el.id === 'computerActions') {
        list[0].els.push(el); return;
      }
      const head = el.matches('section') && (el.querySelector(':scope > details > summary') || el.querySelector(':scope > h2'));
      if (!head) { el.dataset.ptOther = '1'; return; }   // footer, sign-out row
      const name = ownText(head);
      if (!name) return;
      list.push({id: slug(name), name, els: [el], icon: head.querySelector('svg'), head});
      after = true;
    });
    return after ? list : list;
  }

  function link(page) {
    const a = document.createElement('a');
    a.href = '#' + page.id;
    if (page.icon) a.appendChild(page.icon.cloneNode(true));
    else a.insertAdjacentHTML('afterbegin', page.id === MAP
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent)"><path d="M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent)"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>');
    a.appendChild(document.createTextNode(page.name));
    a.dataset.page = page.id;
    a.addEventListener('click', e => { e.preventDefault(); show(page.id); });
    return a;
  }

  function buildNav(list) {
    const items = nav.querySelector('.pt-items');
    items.innerHTML = '';
    items.appendChild(link(list[0]));
    items.appendChild(link({id: MAP, name: 'Live Map'}));
    items.appendChild(Object.assign(document.createElement('div'), {className: 'pt-sep'}));
    list.slice(1).forEach(p => items.appendChild(link(p)));
    nav.querySelector('.pt-brand b').textContent = (document.getElementById('serverName') || {}).textContent || 'PortableTAK';
  }

  function show(id) {
    const list = pages();
    if (id !== MAP && !list.some(p => p.id === id)) id = DASHBOARD;
    current = id; store.set(id);
    if (location.hash !== '#' + id) history.replaceState(null, '', '#' + id);
    list.forEach(p => p.els.forEach(el => {
      const on = p.id === id;
      el.classList.toggle('pt-hide', !on);
      el.classList.toggle('pt-page', on && p.id !== DASHBOARD);
      const det = el.querySelector(':scope > details');
      if (on && det && !det.open) { det.open = true; opened.add(det); }   // its card loads its data on open
    }));
    main().querySelectorAll('[data-pt-other]').forEach(el => el.classList.add('pt-hide'));
    if (id === MAP) {
      if (!mapPage.querySelector('iframe')) mapPage.innerHTML = '<iframe src="/map" title="Live Map"></iframe>';
      mapPage.classList.remove('pt-hide');
    } else mapPage.classList.add('pt-hide');
    const page = list.find(p => p.id === id);
    title.innerHTML = '';
    const a = nav.querySelector('a[data-page="' + id + '"]');
    if (a && a.querySelector('svg')) title.appendChild(a.querySelector('svg').cloneNode(true));
    title.appendChild(document.createTextNode(id === MAP ? 'Live Map' : page.name));
    nav.querySelectorAll('a[data-page]').forEach(x => x.classList.toggle('active', x.dataset.page === id));
    window.scrollTo(0, 0);
  }

  function enable() {
    if (nav) return;
    document.body.classList.add('pt-desk');
    nav = document.createElement('nav');
    nav.id = 'ptNav';
    nav.innerHTML = '<div class="pt-brand"><b></b><span>PortableTAK</span></div><div class="pt-items"></div><div class="pt-foot"></div>';
    const out = document.createElement('a');
    out.insertAdjacentHTML('afterbegin', '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="color:var(--muted)"><path d="M14 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2v-3"/><path d="M9 12h12M18 9l3 3-3 3"/></svg>');
    out.appendChild(document.createTextNode('Sign out'));
    out.addEventListener('click', e => {
      e.preventDefault();
      const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === 'Sign out');
      if (b) b.click();
    });
    nav.querySelector('.pt-foot').appendChild(out);
    document.body.appendChild(nav);
    title = document.createElement('div'); title.id = 'ptTitle';
    mapPage = document.createElement('div'); mapPage.id = 'ptMap'; mapPage.className = 'pt-hide';
    const header = main().querySelector(':scope > header');
    header.after(title); title.after(mapPage);
    buildNav(pages());
    show((location.hash || '').slice(1) || store.get() || DASHBOARD);
    // The server name loads after the page: keep the menu's name in step.
    const name = document.getElementById('serverName');
    if (name) new MutationObserver(() => { if (nav) nav.querySelector('.pt-brand b').textContent = name.textContent; })
      .observe(name, {childList: true, characterData: true, subtree: true});
    // Cards added later (Remote Access, Live Feeds, ...): add them to the menu.
  }

  function changed() {
    clearTimeout(timer);
    timer = setTimeout(() => { order(); if (nav) { buildNav(pages()); show(current); } }, 60);
  }

  function disable() {
    if (!nav) return;
    document.body.classList.remove('pt-desk');
    nav.remove(); title.remove(); mapPage.remove(); nav = null;
    main().querySelectorAll('.pt-hide, .pt-page').forEach(el => el.classList.remove('pt-hide', 'pt-page'));
    opened.forEach(d => { d.open = false; }); opened = new Set();
  }

  const apply = () => { order(); if (WIDE.matches) enable(); else disable(); };
  window.addEventListener('hashchange', () => { if (nav) show(location.hash.slice(1)); });
  WIDE.addEventListener('change', apply);
  // After the other scripts have added their cards and icons.
  const go = () => setTimeout(() => {
    apply();
    // Cards added later (Remote Access, Live Feeds, ...): put them in place (and in the menu).
    observer = new MutationObserver(changed);
    observer.observe(main(), {childList: true});
  }, 150);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();

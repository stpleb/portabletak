// The logo at the top of the page is its own control: for a signed-in administrator, it and the
// three dots beside it open a small menu to change the logo or go back to the default.
(function () {
  const ERRORS = {logo_too_large: 'That image is too big (2 MB at most).', not_an_image: 'Use a PNG, JPG or WebP image.',
    empty_file: 'That file is empty.', no_drive: 'No drive is connected.'};
  let custom = false, menu = null, note = null, noteTimer = null;

  const pick = document.createElement('input');
  pick.type = 'file'; pick.accept = 'image/png,image/jpeg,image/webp'; pick.hidden = true;

  function say(text, anchor) {
    if (note) note.remove();
    clearTimeout(noteTimer);
    note = document.createElement('div');
    note.className = 'pt-logo-pop'; note.textContent = text;
    place(note, anchor);
    noteTimer = setTimeout(() => { if (note) { note.remove(); note = null; } }, 5000);
  }

  function place(pop, anchor) {
    const box = (anchor || document.querySelector('.pt-logo')).getBoundingClientRect();
    pop.style.left = Math.max(8, box.left) + 'px';
    pop.style.top = box.bottom + 6 + 'px';
    document.body.appendChild(pop);
  }

  function shown(isCustom) {
    custom = isCustom;
    document.querySelectorAll('.pt-logo img').forEach(img => { img.src = '/logo?t=' + Date.now(); });
  }

  function closeMenu() { if (menu) { menu.remove(); menu = null; } }

  function openMenu(anchor) {
    closeMenu();
    menu = document.createElement('div');
    menu.className = 'pt-logo-pop'; menu.setAttribute('role', 'menu');
    const item = (label, act) => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = label; b.setAttribute('role', 'menuitem');
      b.addEventListener('click', () => { closeMenu(); act(); });
      menu.appendChild(b);
    };
    item('Change logo', () => { pick.anchor = anchor; pick.click(); });
    if (custom) item('Reset to default', async () => {
      const r = await fetch('/api/logo', {method: 'DELETE'});
      if (r.ok) shown(false); else say('Could not reset the logo.', anchor);
    });
    place(menu, anchor);
    menu.querySelector('button').focus();
  }

  pick.addEventListener('change', async () => {
    const file = pick.files[0], anchor = pick.anchor;
    pick.value = '';
    if (!file) return;
    if (file.size > 2097152) { say(ERRORS.logo_too_large, anchor); return; }
    const r = await fetch('/api/logo', {method: 'PUT', headers: {'Content-Type': 'application/octet-stream'}, body: file})
      .catch(() => null);
    if (r && r.ok) { shown(true); return; }
    const data = r ? await r.json().catch(() => ({})) : {};
    say(ERRORS[(data.detail || {}).error] || 'Could not save the logo.', anchor);
  });

  async function start() {
    const r = await fetch('/api/logo').catch(() => null);
    if (!r || !r.ok) return;   // not signed in: the logo stays a plain picture
    custom = !!(await r.json()).custom;
    document.body.classList.add('pt-logo-edit');
    document.body.appendChild(pick);
    document.addEventListener('click', e => {
      const anchor = e.target.closest && e.target.closest('.pt-logo');
      if (!anchor) { if (!(menu && menu.contains(e.target))) closeMenu(); return; }
      if (menu) { closeMenu(); return; }
      openMenu(anchor);
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();

// "Remote access" card: enable Tailscale for this cartridge from the dashboard.
(function () {
  const api = (path, method) => fetch('/api/remote-access' + path, {method: method || 'GET'})
    .then(r => r.json().catch(() => ({})));
  // The server restarts during Finish setup / Sign out, which cuts the request off.
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const tryApi = (path, method) => api(path, method).catch(() => null);
  async function waitForServer() {
    for (let i = 0; i < 60; i++) {
      await sleep(5000);
      const s = await tryApi('');
      if (s && s.PORTABLETAK_TAILSCALE) return true;
    }
    return false;
  }
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  function build() {
    // Same markup as the dashboard's other collapsible sections (e.g. Backup / Restore).
    const card = el('section', 'margin-top:18px');
    card.className = 'card';
    const details = el('details');
    const summary = el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Remote Access');
    const body = el('div', 'margin-top:14px');
    details.append(summary, body);
    card.appendChild(details);
    const help = el('div', 'margin:0 0 10px;font-size:0.92em;opacity:0.85');
    const status = el('div', 'margin-bottom:10px', 'Checking…');
    const link = el('a', 'display:none;word-break:break-all;margin:8px 0');
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    const enable = el('button', 'margin-right:8px', 'Enable remote access');
    const finish = el('button', 'display:none', 'Finish setup');
    const logout = el('button', 'display:none;margin-left:8px', 'Sign out of Tailscale');
    body.append(help, status, link, enable, finish, logout);
    details.addEventListener('toggle', () => { if (details.open) refresh(); });
    // The drive has its own Tailscale identity, so its remote address is the same on
    // every node and computer it runs on: phones set up once keep working.
    const HELP = 'Remote access lets phones reach this server over cellular or any Wi-Fi, using Tailscale ' +
      '(a free private network). The drive keeps its own Tailscale address wherever it runs (node, Windows ' +
      'or Mac). You need: internet where the server runs, a free Tailscale account, and the Tailscale app on ' +
      'each phone signed in to that same account. Steps: 1) Enable remote access. 2) Open the link, sign in and ' +
      'approve the device. 3) Press Finish setup. 4) Set up phones afterwards: ATAK QR Setup or ATAK Package ' +
      '(both add the Tailscale connection); on iPhone also the iTAK Remote Package.';
    async function refresh() {
      const s = await api('');
      const state = s.PORTABLETAK_TAILSCALE;
      help.textContent = HELP;
      if (state === 'RUNNING' && s.PORTABLETAK_TAILSCALE_IP) {
        status.textContent = 'On. Remote clients use ' + s.PORTABLETAK_TAILSCALE_IP + ' (phones need the Tailscale app).';
        enable.style.display = 'none'; finish.style.display = 'inline-block'; finish.textContent = 'Update server certificate';
        logout.style.display = 'inline-block';
      } else if (state === 'STOPPED') {
        status.textContent = 'Set up, but not connected right now (check internet).';
        enable.style.display = 'inline-block'; logout.style.display = 'inline-block';
      } else if (state === 'NOT_INSTALLED') {
        status.textContent = 'Tailscale is not installed on this host.'; enable.disabled = true;
      } else {
        status.textContent = 'Off. Lets phones reach this server over cellular or any Wi-Fi.';
        enable.style.display = 'inline-block'; finish.style.display = 'none'; logout.style.display = 'none';
      }
    }
    enable.onclick = async () => {
      enable.disabled = true; status.textContent = 'Starting Tailscale…';
      const r = await api('/enable', 'POST');
      if (r.PORTABLETAK_TAILSCALE_LOGIN_URL) {
        link.href = r.PORTABLETAK_TAILSCALE_LOGIN_URL; link.textContent = r.PORTABLETAK_TAILSCALE_LOGIN_URL; link.style.display = 'block';
        status.textContent = 'Open this link, sign in and approve the device, then press Finish setup.';
        finish.style.display = 'inline-block';
      } else if (r.PORTABLETAK_TAILSCALE === 'LOGGED_IN') {
        status.textContent = 'Already joined as ' + r.PORTABLETAK_TAILSCALE_IP + '. Press Finish setup.'; finish.style.display = 'inline-block';
      } else {
        status.textContent = 'Could not start: ' + (r.PORTABLETAK_TAILSCALE_ERROR || r.PORTABLETAK_TAILSCALE || 'unknown error') + '. Check that this computer or node has internet.';
      }
      enable.disabled = false;
    };
    finish.onclick = async () => {
      finish.disabled = true; status.textContent = 'Adding the address to the certificate and restarting the server (about 2 minutes)…';
      let r = await tryApi('/apply', 'POST');
      if (!r || !r.PORTABLETAK_TAILSCALE && !r.PORTABLETAK_TAILSCALE_ERROR) {
        status.textContent = 'The server is restarting. This page updates by itself (about 2 minutes)…';
        await waitForServer();
        await refresh();
        r = {PORTABLETAK_TAILSCALE: 'READY', PORTABLETAK_TAILSCALE_IP: (await tryApi('') || {}).PORTABLETAK_TAILSCALE_IP || ''};
      }
      status.textContent = r.PORTABLETAK_TAILSCALE === 'READY'
        ? 'Ready. Remote clients: ' + r.PORTABLETAK_TAILSCALE_IP + (r.PORTABLETAK_TAILSCALE_NAME ? ' or ' + r.PORTABLETAK_TAILSCALE_NAME : '')
        : (r.PORTABLETAK_TAILSCALE_ERROR === 'not_connected_check_internet'
           ? "The drive's Tailscale is not connected yet. Check this computer's or node's internet, wait a minute and try again."
           : 'Setup did not finish: ' + (r.PORTABLETAK_TAILSCALE_ERROR || 'unknown error'));
      link.style.display = 'none'; finish.disabled = false;
    };
    logout.onclick = async () => {
      if (!confirm('Sign this server out of its Tailscale account?\n\nPhones on cellular stop reaching it until remote ' +
                   'access is set up again (any account) and the phones are set up again with a new QR or package.')) return;
      logout.disabled = true; finish.disabled = true;
      status.textContent = 'Signing out and restarting the server (about 2 minutes)…';
      let r = await tryApi('/logout', 'POST');
      if (!r || !r.PORTABLETAK_TAILSCALE && !r.PORTABLETAK_TAILSCALE_ERROR) {
        status.textContent = 'The server is restarting. This page updates by itself (about 2 minutes)…';
        await waitForServer();
        r = {PORTABLETAK_TAILSCALE: 'LOGGED_OUT'};
      }
      logout.disabled = false; finish.disabled = false;
      if (r.PORTABLETAK_TAILSCALE === 'LOGGED_OUT' || r.PORTABLETAK_TAILSCALE === 'NOT_CONFIGURED') {
        await refresh();
        status.textContent = 'Signed out. Press Enable remote access to join a Tailscale account.';
      } else {
        status.textContent = 'Sign-out did not finish: ' + (r.PORTABLETAK_TAILSCALE_ERROR || 'unknown error');
      }
    };
    // Place it directly before Client Setup: remote access comes first in the workflow
    // (packages made afterwards include the remote address).
    const heading = Array.from(document.querySelectorAll('section > h2, section summary'))
      .find(h => h.textContent.trim() === 'Client Setup');
    const clientSection = heading && heading.closest('section');
    if (clientSection) clientSection.before(card); else (document.querySelector('main') || document.body).appendChild(card);
    refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();

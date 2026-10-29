// Management sign-in. Adds the CSRF header to state-changing requests and shows
// a sign-in panel when the API enforces sessions. Does nothing in 'off' mode.
(function () {
  const nativeFetch = window.fetch.bind(window);
  const unsafe = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  let csrf = null;
  let mode = 'off';
  let panel = null;

  window.fetch = async function (input, init) {
    init = Object.assign({}, init || {});
    const method = (init.method || (input && input.method) || 'GET').toUpperCase();
    if (csrf && unsafe.has(method)) {
      const headers = new Headers(init.headers || {});
      headers.set('X-CSRF-Token', csrf);
      init.headers = headers;
    }
    init.credentials = 'same-origin';
    const response = await nativeFetch(input, init);
    if (response.status === 401 && mode === 'enforce') showSignIn();
    return response;
  };

  function element(tag, style, text) {
    const node = document.createElement(tag);
    if (style) node.style.cssText = style;
    if (text) node.textContent = text;
    return node;
  }

  function showSignIn(message) {
    if (panel) { panel.error.textContent = message || ''; return; }
    const shade = element('div', 'position:fixed;inset:0;z-index:1000;display:flex;align-items:center;' +
      'justify-content:center;background:rgba(0,0,0,.65);padding:16px');
    const box = element('form', 'width:100%;max-width:360px;background:var(--panel,#121922);' +
      'border:1px solid var(--border,#293748);border-radius:10px;padding:20px;color:var(--text,#e8eef5);' +
      'font-family:inherit');
    box.appendChild(element('h2', 'margin:0 0 6px;font-size:18px', 'PortableTAK Server'));
    box.appendChild(element('div', 'color:var(--muted,#8fa1b5);font-size:13px;margin-bottom:14px',
      'Sign in with the administrator password chosen during setup.'));
    const password = element('input', 'width:100%;box-sizing:border-box;padding:10px;border-radius:6px;' +
      'border:1px solid var(--border,#293748);background:var(--bg,#0b0f14);color:inherit;font:inherit');
    password.type = 'password';
    password.autocomplete = 'current-password';
    password.placeholder = 'Administrator password';
    box.appendChild(password);
    const error = element('div', 'color:var(--bad,#ef6b73);font-size:13px;min-height:18px;margin:8px 0');
    error.textContent = message || '';
    box.appendChild(error);
    const button = element('button', 'width:100%', 'Sign in');
    button.className = 'primary';
    button.type = 'submit';
    box.appendChild(button);
    box.addEventListener('submit', async function (event) {
      event.preventDefault();
      button.disabled = true;
      error.textContent = '';
      try {
        const response = await nativeFetch('/api/auth/login', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          // client_time_ms: a node with no internet sets its clock from this (when it is off).
          body: JSON.stringify({username: 'administrator', password: password.value, client_time_ms: Date.now()})
        });
        const body = await response.json().catch(() => ({}));
        if (response.ok) { window.location.reload(); return; }
        error.textContent = {
          invalid_credentials: 'Incorrect password.',
          too_many_attempts: 'Too many attempts. Try again in ' + (body.retry_after || 60) + ' seconds.',
          management_auth_not_initialized: 'This cartridge has not completed setup.'
        }[body.detail] || 'Sign-in failed.';
      } catch (failure) {
        error.textContent = 'Server unreachable.';
      } finally {
        password.value = '';
        button.disabled = false;
      }
    });
    shade.appendChild(box);
    document.body.appendChild(shade);
    panel = {error: error};
    password.focus();
  }

  // At the very bottom of the page (below Erase Drive), not floating over the content.
  function showSignOut() {
    function place() {
      const wrap = element('div', 'text-align:center;margin:24px 0 32px');
      const link = element('button', '', 'Sign out');
      link.addEventListener('click', async function () {
        await window.fetch('/api/auth/logout', {method: 'POST'});
        window.location.reload();
      });
      wrap.appendChild(link);
      (document.querySelector('main') || document.body).appendChild(wrap);
    }
    // After the deferred scripts have added their cards (Erase Drive is the last).
    if (document.readyState === 'complete') place(); else window.addEventListener('load', place);
  }

  async function start() {
    try {
      const response = await nativeFetch('/api/auth/session', {credentials: 'same-origin'});
      const session = await response.json();
      mode = session.mode;
      if (session.authenticated) {
        csrf = session.csrf;
        if (mode === 'enforce') showSignOut();
      } else if (mode === 'enforce') {
        showSignIn();
      }
    } catch (failure) {
      // Older API without the guard: leave the dashboard unchanged.
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();

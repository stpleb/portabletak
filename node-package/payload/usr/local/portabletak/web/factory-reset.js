// "Erase Drive" card: return this PortableTAK drive to its first-run state.
(function () {
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  function build() {
    const card = el('section', 'margin-top:18px');
    card.className = 'card';
    const details = el('details');
    const summary = el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Erase Drive');
    const body = el('div', 'margin-top:14px');
    details.append(summary, body);
    card.appendChild(details);
    body.appendChild(el('p', 'color:#ffb3b3;margin:0 0 10px',
      'Erases this drive back to how it was before first setup. Deletes ALL users, ' +
      'certificates, settings, remote access, data and the backups stored on this drive. ' +
      'Phones set up for this server will stop working. This cannot be undone.'));
    body.appendChild(el('p', 'margin:0 0 10px',
      'Afterwards, set the drive up again with PortableTAK Setup on a Windows or Mac computer.'));
    const password = el('input', 'width:100%;box-sizing:border-box;padding:10px;margin-bottom:8px');
    password.type = 'password'; password.placeholder = 'Administrator password'; password.autocomplete = 'current-password';
    const confirm = el('input', 'width:100%;box-sizing:border-box;padding:10px;margin-bottom:8px');
    confirm.placeholder = 'Type ERASE to confirm'; confirm.autocomplete = 'off';
    const button = el('button', 'background:#b3261e;color:white', 'Erase this drive');
    const status = el('div', 'margin-top:10px');
    body.append(password, confirm, button, status);
    button.disabled = true;
    confirm.addEventListener('input', () => { button.disabled = confirm.value !== 'ERASE'; });
    button.addEventListener('click', async () => {
      if (!window.confirm('Erase this PortableTAK drive now? This cannot be undone.')) return;
      button.disabled = true;
      status.textContent = 'Erasing… this takes a minute. Do not unplug the drive.';
      try {
        const r = await fetch('/api/factory-reset', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({password: password.value, confirm: confirm.value})});
        const data = await r.json().catch(() => ({}));
        password.value = '';
        if (r.ok && data.ok) {
          status.textContent = 'Done. The drive is blank again. Set it up with PortableTAK Setup on a computer.';
          confirm.value = '';
        } else {
          const why = {incorrect_password: 'Incorrect administrator password.',
            type_ERASE_to_confirm: 'Type ERASE to confirm.',
            lifecycle_action_in_progress: 'Another action is running; try again in a minute.'}[data.detail];
          status.textContent = why || 'Erase failed: ' + (typeof data.detail === 'string' ? data.detail : 'see the server log');
          button.disabled = confirm.value !== 'ERASE';
        }
      } catch (e) {
        status.textContent = 'Erase request failed: ' + e.message;
        button.disabled = confirm.value !== 'ERASE';
      }
    });
    (document.querySelector('main') || document.body).appendChild(card);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();

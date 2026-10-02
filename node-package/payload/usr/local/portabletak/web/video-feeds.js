// "Video Feeds" card: drones (e.g. Skydio X10D) push RTSP video to this server.
(function () {
  const call = (path, method, body) => fetch('/api/video-feeds' + path, {
    method: method || 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const NAME = /^[a-z0-9][a-z0-9-]{0,31}$/;

  function build() {
    // Same markup as the dashboard's other collapsible sections (e.g. Remote Access).
    const card = el('section', 'margin-top:18px');
    card.className = 'card';
    const details = el('details');
    const summary = el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Video Feeds');
    const body = el('div', 'margin-top:14px');
    details.append(summary, body);
    card.appendChild(details);

    const help = el('div', 'margin:0 0 12px;font-size:0.92em;opacity:0.85',
      'Lets a drone send live video to this server, the way a Video Alias does in Sit(x). ' +
      'Add a feed name here, enter the same name and this server\'s address in the drone\'s RTSP sharing settings, ' +
      'and every TAK client sees the feed in its video list. Feeds need no sign-in to send or watch, ' +
      'so anyone who can reach this server can watch them.');
    const list = el('div', 'margin-bottom:12px');
    const row = el('div', 'display:flex;gap:8px;flex-wrap:wrap;align-items:center');
    const input = el('input', 'flex:1;min-width:160px');
    input.placeholder = 'Feed name, e.g. skydio1';
    input.autocapitalize = 'off'; input.autocomplete = 'off'; input.spellcheck = false;
    const add = el('button', '', 'Add feed');
    row.append(input, add);
    const status = el('div', 'margin-top:8px;font-size:0.9em;min-height:1.2em');
    const steps = el('div', 'margin-top:14px;font-size:0.9em;line-height:1.5;display:none');
    const bridge = el('details', 'margin-top:16px');
    const bridgeTitle = el('summary', 'cursor:pointer;font-weight:600', 'Drone far from this server (phone hotspot bridge)');
    const bridgeBody = el('div', 'margin-top:8px;font-size:0.9em;line-height:1.5');
    bridge.append(bridgeTitle, bridgeBody);
    body.append(help, list, row, status, steps, bridge);

    // The drone controller joins an ATAK phone's hotspot; a free app on that phone
    // (Termux) forwards the TAK (8089) and video (8554) ports to this server over the
    // phone's own Tailscale. Nothing is installed on the controller.
    function bridgeHelp(info) {
      bridgeBody.innerHTML = '';
      if (!info.remote) {
        bridgeBody.append(el('div', '', 'Set up Remote Access first (above): the bridge reaches this server through Tailscale.'));
        return;
      }
      const command = 'termux-wake-lock; yes | pkg install socat >/dev/null 2>&1; ' +
        'kill $(cat ~/.ptbridge 2>/dev/null) 2>/dev/null; : > ~/.ptbridge; ' +
        'for p in 8089 8554; do socat TCP-LISTEN:$p,fork,reuseaddr TCP:' + info.remote + ':$p & echo $! >> ~/.ptbridge; done; ' +
        'sleep 1; echo; echo "PortableTAK bridge running. Drone address:"; ' +
        "ifconfig 2>/dev/null | awk '/^[a-z]/{i=$1} /inet /{print i, $2}' | grep -E '^(swlan|ap|softap|wlan)' ; " +
        'echo "Leave Termux open while flying."';
      bridgeBody.append(
        el('div', '', 'Use this when the drone team is on cellular and this server is somewhere else. ' +
           'The drone controller joins the hotspot of an ATAK phone; that phone forwards the drone\'s TAK and video ' +
           'traffic to this server over its Tailscale. Nothing is installed on the controller.'));
      const ol = el('ol', 'margin:8px 0 0 18px;padding:0');
      [
        'On the phone, once: install Termux (free, from Google Play or F-Droid). Tailscale must already be set up on the phone.',
        'Each time: turn on the phone\'s hotspot and Tailscale, open Termux, paste the command below and press Enter.',
        'Termux shows the drone address (the hotspot line, e.g. "swlan0: 192.168.43.1"). If it shows none, use the Gateway in the controller\'s Wi-Fi details. Use it on the controller as the TAK server IP (port 8089) and as the RTSP External IP (port 8554).',
        'Join the controller to the phone\'s hotspot. Leave Termux open while flying.'
      ].forEach(t => ol.appendChild(el('li', '', t)));
      const box = el('textarea', 'width:100%;height:96px;margin-top:8px;font-family:monospace;font-size:0.85em');
      box.readOnly = true; box.value = command;
      const copy = el('button', 'margin-top:6px', 'Copy command');
      copy.onclick = async () => {
        try { await navigator.clipboard.writeText(command); copy.textContent = 'Copied'; }
        catch (e) { box.select(); copy.textContent = 'Select all and copy'; }
      };
      bridgeBody.append(ol, box, copy);
    }

    function skydioSteps(feed, info) {
      const local = info.local || '<this server\'s address>';
      steps.innerHTML = '';
      steps.append(el('div', 'font-weight:600;margin-bottom:4px', 'On the Skydio X10D controller'));
      const ol = el('ol', 'margin:0 0 0 18px;padding:0');
      [
        'Menu (three lines, top left) > Sharing > RTSP > External.',
        'Stream name: ' + feed,
        'IP address: ' + local + '  (the controller must be on the same Wi-Fi or mesh as this server)',
        'Port: ' + info.port,
        'Start Stream. The video appears once the aircraft is flying.'
      ].forEach(t => ol.appendChild(el('li', '', t)));
      steps.appendChild(ol);
      steps.append(el('div', 'margin-top:8px;opacity:0.85',
        'Watch it: in ATAK or WinTAK open Video and pick "' + feed + '" (it plays while the drone is streaming). ' +
        'Or add rtsp://' + local + ':' + info.port + '/' + feed + ' by hand.'));
      steps.style.display = 'block';
    }

    async function refresh() {
      const r = await call('');
      list.innerHTML = '';
      const info = r.data || {};
      const feeds = info.feeds || [];
      bridgeHelp(info);
      if (!feeds.length) list.appendChild(el('div', 'opacity:0.75', 'No video feeds yet.'));
      feeds.forEach(f => {
        const line = el('div', 'display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.08)');
        const dot = el('span', 'width:10px;height:10px;border-radius:50%;background:' + (f.live ? '#3fb950' : '#6e7681'));
        const name = el('span', 'font-weight:600;flex:1', f.name);
        const state = el('span', 'font-size:0.85em;opacity:0.8', f.live ? 'Live' : 'Waiting for video');
        const how = el('button', '', 'Drone settings');
        how.onclick = () => skydioSteps(f.name, info);
        const del = el('button', '', 'Delete');
        del.onclick = async () => {
          if (!confirm('Delete the video feed "' + f.name + '"? The drone can no longer send to it.')) return;
          await call('/' + encodeURIComponent(f.name), 'DELETE');
          steps.style.display = 'none';
          refresh();
        };
        line.append(dot, name, state, how, del);
        list.appendChild(line);
      });
    }

    add.onclick = async () => {
      const name = input.value.trim().toLowerCase();
      if (!NAME.test(name)) {
        status.textContent = 'Use lowercase letters, numbers and dashes only (e.g. skydio1).';
        return;
      }
      add.disabled = true; status.textContent = 'Adding…';
      const r = await call('', 'POST', {name});
      add.disabled = false;
      if (r.ok) {
        status.textContent = 'Added "' + name + '". Now set up the drone:';
        input.value = '';
        await refresh();
        const info = (await call('')).data || {};
        skydioSteps(name, info);
      } else {
        const why = {invalid_feed_name: 'That name cannot be used.', too_many_feeds: 'Too many feeds (16 at most).',
                     video_server_not_running: 'Saved, but the video server is not running; it applies at the next start.'};
        status.textContent = why[r.data.detail] || ('Could not add the feed: ' + (r.data.detail || 'unknown error'));
        refresh();
      }
    };
    details.addEventListener('toggle', () => { if (details.open) refresh(); });

    // Directly before Client Setup, after Remote Access.
    const heading = Array.from(document.querySelectorAll('section > h2, section summary'))
      .find(h => h.textContent.trim() === 'Client Setup');
    const clientSection = heading && heading.closest('section');
    if (clientSection) clientSection.before(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();

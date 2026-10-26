// "Video Feeds" card: drones (e.g. Skydio X10D) push RTSP video to this server.
(function () {
  const call = (path, method, body) => fetch('/api/video-feeds' + path, {
    method: method || 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}));
  function el(tag, css, text) { const n = document.createElement(tag); if (css) n.style.cssText = css; if (text) n.textContent = text; return n; }
  const NAME = /^[a-z0-9][a-z0-9-]{0,31}$/;
  const cell = 'padding:4px 10px 4px 0;vertical-align:top';

  function table(rows) {
    const t = el('table', 'border-collapse:collapse;margin:6px 0');
    rows.forEach(([k, v]) => {
      const tr = el('tr');
      tr.append(el('td', cell + ';opacity:0.75;white-space:nowrap', k), el('td', cell + ';font-weight:600', v));
      t.appendChild(tr);
    });
    return t;
  }

  // In-browser player: the dashboard relays the feed's HLS (/api/video/hls/<name>/index.m3u8).
  window.ptWatchVideo = function (feed) {
    const shade = el('div', 'position:fixed;inset:0;z-index:50;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;padding:16px');
    const box = el('div', 'width:min(960px,100%);background:#121922;border:1px solid #293748;border-radius:12px;padding:12px');
    const head = el('div', 'display:flex;align-items:center;gap:10px;margin-bottom:8px');
    const title = el('b', 'flex:1', feed);
    const note = el('span', 'font-size:0.85em;opacity:0.8', 'Connecting\u2026');
    const close = el('button', '', 'Close');
    head.append(title, note, close);
    const video = el('video', 'width:100%;max-height:75vh;background:#000;border-radius:8px');
    video.controls = true; video.autoplay = true; video.muted = true; video.playsInline = true;
    box.append(head, video); shade.appendChild(box); document.body.appendChild(shade);
    const src = '/api/video/hls/' + encodeURIComponent(feed) + '/index.m3u8';
    let hls = null;
    const done = () => { if (hls) hls.destroy(); video.pause(); shade.remove(); };
    close.onclick = done; shade.onclick = (e) => { if (e.target === shade) done(); };
    video.addEventListener('playing', () => { note.textContent = 'Live'; });
    const start = () => {
      if (window.Hls && window.Hls.isSupported()) {
        hls = new window.Hls({ lowLatencyMode: true, liveSyncDurationCount: 2 });
        hls.on(window.Hls.Events.ERROR, (_, data) => { if (data.fatal) note.textContent = 'Video not available (' + data.details + ')'; });
        hls.loadSource(src); hls.attachMedia(video);
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = src;   // iPhone / Safari play HLS natively
      } else {
        note.textContent = 'This browser cannot play the video.';
      }
    };
    if (window.Hls || video.canPlayType('application/vnd.apple.mpegurl')) start();
    else { const sc = document.createElement('script'); sc.src = '/mapkit/hls.min.js'; sc.onload = start;
      sc.onerror = () => { note.textContent = 'Player did not load.'; }; document.head.appendChild(sc); }
  };

  function build() {
    // Same markup as the dashboard's other collapsible sections.
    const card = el('section', 'margin-top:18px');
    card.className = 'card';
    const details = el('details');
    details.append(el('summary', 'cursor:pointer;color:white;font-size:1.35rem;font-weight:600', 'Video Feeds'));
    const body = el('div', 'margin-top:14px');
    details.appendChild(body);
    card.appendChild(details);

    const help = el('div', 'margin:0 0 12px;font-size:0.92em;opacity:0.85',
      'Live drone video for every TAK user. Add a feed, then set the drone to send to it.');
    const list = el('div', 'margin-bottom:12px');
    const row = el('div', 'display:flex;gap:8px;flex-wrap:wrap;align-items:center');
    const input = el('input', 'flex:1;min-width:160px');
    input.placeholder = 'Feed name, e.g. skydio1';
    input.autocapitalize = 'off'; input.autocomplete = 'off'; input.spellcheck = false;
    const add = el('button', '', 'Add feed');
    row.append(input, add);
    const status = el('div', 'margin-top:8px;font-size:0.9em;min-height:1.2em');

    const drone = el('details', 'margin-top:12px;font-size:0.9em');
    drone.append(el('summary', 'cursor:pointer;font-weight:600', 'Drone settings (e.g. Skydio X10D)'));
    const droneBody = el('div', 'margin-top:6px');
    drone.appendChild(droneBody);

    const bridge = el('details', 'margin-top:10px;font-size:0.9em');
    bridge.append(el('summary', 'cursor:pointer;font-weight:600', 'Drone far from this server (phone hotspot)'));
    const bridgeBody = el('div', 'margin-top:6px;line-height:1.5');
    bridge.appendChild(bridgeBody);

    body.append(help, list, row, status, drone, bridge);

    function droneSettings(info, feeds) {
      droneBody.innerHTML = '';
      const name = feeds.length === 1 ? feeds[0].name : (feeds.length ? 'the feed name' : 'the feed name (add one above)');
      droneBody.append(
        el('div', 'opacity:0.85', 'On the controller: Sharing → RTSP → External'),
        table([['Stream name', name], ['IP address', info.local || "this server's address"], ['Port', String(info.port || 8554)]]),
        el('div', 'opacity:0.85', 'Start Stream; video appears once the aircraft is flying. ' +
           'Watch it in ATAK/WinTAK under Video. The controller must be on the same network as this server.'));
    }

    // A free app (Termux) on the ATAK phone forwards the drone's TAK (8089) and video
    // (8554) traffic to this server over the phone's own Tailscale.
    function bridgeHelp(info) {
      bridgeBody.innerHTML = '';
      if (!info.remote) {
        bridgeBody.append(el('div', 'opacity:0.85', 'Set up Remote Access first.'));
        return;
      }
      const command = 'termux-wake-lock; yes | pkg install socat >/dev/null 2>&1; ' +
        'kill $(cat ~/.ptbridge 2>/dev/null) 2>/dev/null; : > ~/.ptbridge; ' +
        'for p in 8089 8554; do socat TCP-LISTEN:$p,fork,reuseaddr TCP:' + info.remote + ':$p & echo $! >> ~/.ptbridge; done; ' +
        'sleep 1; echo; echo "PortableTAK bridge running. Drone address:"; ' +
        "ifconfig 2>/dev/null | awk '/^[a-z]/{i=$1} /inet /{print i, $2}' | grep -E '^(swlan|ap|softap|wlan)' ; " +
        'echo "Leave Termux open while flying."';
      const box = el('textarea', 'width:100%;height:84px;margin-top:6px;font-family:monospace;font-size:0.85em');
      box.readOnly = true; box.value = command;
      const copy = el('button', 'margin-top:6px', 'Copy command');
      copy.onclick = async () => {
        try { await navigator.clipboard.writeText(command); copy.textContent = 'Copied'; }
        catch (e) { box.select(); copy.textContent = 'Select all and copy'; }
      };
      bridgeBody.append(
        el('div', 'opacity:0.85', "The controller joins the ATAK phone's hotspot. On that phone: install Termux once; " +
           'then turn on the hotspot and Tailscale, paste this into Termux, and use the address it prints as the IP ' +
           'in the drone settings and as the TAK server IP.'),
        box, copy);
    }

    async function refresh() {
      const r = await call('');
      const info = r.data || {};
      const feeds = info.feeds || [];
      list.innerHTML = '';
      if (!feeds.length) list.appendChild(el('div', 'opacity:0.75', 'No video feeds yet.'));
      feeds.forEach(f => {
        const line = el('div', 'display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.08)');
        const dot = el('span', 'width:10px;height:10px;border-radius:50%;background:' + (f.live ? '#3fb950' : '#6e7681'));
        const name = el('span', 'font-weight:600;flex:1', f.name);
        const state = el('span', 'font-size:0.85em;opacity:0.8', f.live ? 'Live' : 'Waiting for video');
        const del = el('button', '', 'Delete');
        del.onclick = async () => {
          if (!confirm('Delete the video feed "' + f.name + '"?')) return;
          await call('/' + encodeURIComponent(f.name), 'DELETE');
          refresh();
        };
        const watch = el('button', '', 'Watch');
        watch.className = 'primary';
        watch.onclick = () => window.ptWatchVideo(f.name);
        if (f.live) line.append(dot, name, state, watch, del); else line.append(dot, name, state, del);
        list.appendChild(line);
      });
      droneSettings(info, feeds);
      bridgeHelp(info);
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
        status.textContent = 'Added "' + name + '".';
        input.value = '';
        drone.open = true;
      } else {
        const why = {invalid_feed_name: 'That name cannot be used.', too_many_feeds: 'Too many feeds (16 at most).',
                     video_server_not_running: 'Saved; it takes effect at the next start.'};
        status.textContent = why[r.data.detail] || ('Could not add the feed: ' + (r.data.detail || 'unknown error'));
      }
      refresh();
    };
    details.addEventListener('toggle', () => { if (details.open) refresh(); });

    // Directly after the Users section (feeds go with the accounts they serve).
    const heading = Array.from(document.querySelectorAll('section > h2, section summary'))
      .find(h => h.textContent.trim() === 'Users');
    const usersSection = heading && heading.closest('section');
    if (usersSection) usersSection.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();

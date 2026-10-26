// "Video Feeds" card: drones (e.g. Skydio X10D) push RTSP video to this server; TAK ICU
// pushes RTSP to the hidden input _in/<feed>, which the server unwraps (lib/ts_relay.py);
// cameras and other streams are pulled by the server from their URL.
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

  // WebRTC (WHEP) via the dashboard: under a second behind. Set-up goes through
  // /api/video/whep/<name>; the video comes straight from the video server over UDP.
  async function webrtcPlay(feed, video) {
    const pc = new RTCPeerConnection();
    pc.addTransceiver('video', {direction: 'recvonly'});
    pc.addTransceiver('audio', {direction: 'recvonly'});
    const stream = new MediaStream();
    pc.ontrack = (e) => { stream.addTrack(e.track); video.srcObject = stream; };
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise(resolve => {   // send the offer with its candidates (at most 2 s of gathering)
      if (pc.iceGatheringState === 'complete') return resolve();
      const t = setTimeout(resolve, 2000);
      pc.addEventListener('icegatheringstatechange', () => { if (pc.iceGatheringState === 'complete') { clearTimeout(t); resolve(); } });
    });
    const r = await fetch('/api/video/whep/' + encodeURIComponent(feed), {method: 'POST',
      headers: {'Content-Type': 'application/sdp'}, body: pc.localDescription.sdp});
    if (r.status !== 201) { pc.close(); throw new Error('whep ' + r.status); }
    const session = r.headers.get('Location');
    await pc.setRemoteDescription({type: 'answer', sdp: await r.text()});
    return {pc, close: () => { pc.close(); if (session) fetch(session, {method: 'DELETE'}).catch(() => {}); }};
  }

  // In-browser player: WebRTC first; HLS (relayed: /api/video/hls/<name>/index.m3u8,
  // a few seconds behind) when WebRTC cannot connect or play within a few seconds.
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
    let hls = null, rtc = null, mode = 'WebRTC', closed = false;
    const done = () => { closed = true; if (rtc) rtc.close(); if (hls) hls.destroy(); video.pause(); shade.remove(); };
    close.onclick = done; shade.onclick = (e) => { if (e.target === shade) done(); };
    video.addEventListener('playing', () => { note.textContent = mode === 'WebRTC' ? 'Live' : 'Live (a few seconds behind)'; });
    // hls.js wherever the browser can run it: the browsers' own HLS players (Safari, newer
    // Chrome) stall on streams with uneven timing, such as TAK ICU's.
    const mse = !!(window.MediaSource || window.ManagedMediaSource);
    const start = () => {
      if (closed) return;
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
    const startHls = () => {
      if (closed) return;
      mode = 'HLS';
      if (rtc) { rtc.close(); rtc = null; }
      video.srcObject = null;
      if (window.Hls || (!mse && video.canPlayType('application/vnd.apple.mpegurl'))) start();
      else { const sc = document.createElement('script'); sc.src = '/mapkit/hls.min.js'; sc.onload = start;
        sc.onerror = () => { note.textContent = 'Player did not load.'; }; document.head.appendChild(sc); }
    };
    if (!window.RTCPeerConnection) { startHls(); return; }
    let fallback = setTimeout(startHls, 8000);   // not playing by then: HLS
    video.addEventListener('playing', () => { if (mode === 'WebRTC') clearTimeout(fallback); }, {once: true});
    webrtcPlay(feed, video).then(r => {
      if (closed || mode !== 'WebRTC') { r.close(); return; }
      rtc = r;
      r.pc.addEventListener('connectionstatechange', () => {
        if (mode === 'WebRTC' && r.pc.connectionState === 'failed') { clearTimeout(fallback); startHls(); }
      });
    }).catch(() => { clearTimeout(fallback); startHls(); });
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
      'Live video for every TAK user: drones and apps send to a feed, or the server pulls a camera or stream by its URL.');
    const list = el('div', 'margin-bottom:12px');
    const row = el('div', 'display:flex;gap:8px;flex-wrap:wrap;align-items:center');
    const input = el('input', 'flex:1;min-width:160px');
    input.placeholder = 'Feed name, e.g. skydio1';
    input.autocapitalize = 'off'; input.autocomplete = 'off'; input.spellcheck = false;
    const add = el('button', '', 'Add feed');
    const kind = el('select', 'flex:0 1 auto;width:auto;min-width:0');
    [['push', 'Drone / app sends RTSP'], ['udp', 'TAK ICU'], ['pull', 'Camera / stream URL']].forEach(([v, t]) => {
      const o = el('option', '', t); o.value = v; kind.appendChild(o);
    });
    const url = el('input', 'flex:1 1 100%;min-width:160px;display:none');
    url.placeholder = 'rtsp://user:password@192.168.1.20:554/stream1';
    url.autocapitalize = 'off'; url.autocomplete = 'off'; url.spellcheck = false;
    const urlHelp = el('div', 'flex:1 1 100%;display:none;font-size:0.85em;opacity:0.8;line-height:1.4',
      'The server connects to the camera itself (rtsp://, rtsps://, rtmp://, srt:// or an http(s) .m3u8 link), all the time. ' +
      'The camera must be reachable from the server (same network, or Tailscale). ATAK plays H.264 and H.265; ' +
      'Watch in a browser needs H.264 on most computers. The password stays on the drive and is not shown again.');
    kind.onchange = () => { url.style.display = urlHelp.style.display = kind.value === 'pull' ? 'block' : 'none'; };
    row.append(input, kind, add, url, urlHelp);
    const status = el('div', 'margin-top:8px;font-size:0.9em;min-height:1.2em');

    const drone = el('details', 'margin-top:12px;font-size:0.9em');
    drone.append(el('summary', 'cursor:pointer;font-weight:600', 'Drone settings (e.g. Skydio X10D)'));
    const droneBody = el('div', 'margin-top:6px');
    drone.appendChild(droneBody);

    const bridge = el('details', 'margin-top:10px;font-size:0.9em');
    bridge.append(el('summary', 'cursor:pointer;font-weight:600', 'Drone far from this server (phone hotspot)'));
    const bridgeBody = el('div', 'margin-top:6px;line-height:1.5');
    bridge.appendChild(bridgeBody);

    const icu = el('details', 'margin-top:10px;font-size:0.9em');
    icu.append(el('summary', 'cursor:pointer;font-weight:600', 'TAK ICU settings'));
    const icuBody = el('div', 'margin-top:6px');
    icu.appendChild(icuBody);

    body.append(help, list, row, status, drone, icu, bridge);

    // TAK ICU: Settings > Broadcast Preferences. Local Area Network with RTSP delivery to
    // the feed's hidden input (TCP, nothing lost over Wi-Fi); the server unwraps it.
    function icuSettings(info, feeds) {
      icuBody.innerHTML = '';
      const udp = feeds.filter(f => f.udp_port);
      if (!udp.length) {
        icuBody.append(el('div', 'opacity:0.85', 'Add a feed of type "TAK ICU" (or press "Use for TAK ICU" on a feed); its settings show here.'));
        return;
      }
      icuBody.append(el('div', 'opacity:0.85', 'In TAK ICU: Settings → Broadcast Preferences'));
      udp.forEach(f => icuBody.append(
        el('div', 'font-weight:600;margin-top:8px', f.name),
        table([['Destination Type', 'Local Area Network'], ['Select Delivery Method', 'RTSP'],
          ['RTSP Server Address', (info.local || "this server's address") + ':' + (info.port || 8554) + '/_in/' + f.name]])));
      icuBody.append(el('div', 'opacity:0.85', 'Then press the red button to broadcast. The phone must be on the same network as this server.'));
    }

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
        if (f.udp_port) name.append(el('span', 'font-weight:400;font-size:0.85em;opacity:0.7;margin-left:8px', 'TAK ICU'));
        if (f.source) name.append(el('div', 'font-weight:400;font-size:0.8em;opacity:0.7;word-break:break-all', f.source));
        const mode = el('button', '', f.udp_port ? 'Use for drones' : 'Use for TAK ICU');
        mode.title = f.udp_port ? 'Drones and other RTSP senders' : 'TAK ICU (Local Area Network, RTSP)';
        mode.onclick = async () => {
          const r = await call('', 'POST', {name: f.name, udp: !f.udp_port});
          status.textContent = r.ok ? (r.data.udp_port ? '"' + f.name + '" now takes TAK ICU video (settings below).' : '"' + f.name + '" now takes drone video.')
            : 'Could not change the feed: ' + (r.data.detail || 'unknown error');
          if (r.ok && r.data.udp_port) icu.open = true;
          refresh();
        };
        const state = el('span', 'font-size:0.85em;opacity:0.8', f.live ? 'Live' : (f.source ? 'Connecting to camera' : 'Waiting for video'));
        const del = el('button', '', 'Delete');
        del.onclick = async () => {
          if (!confirm('Delete the video feed "' + f.name + '"?')) return;
          await call('/' + encodeURIComponent(f.name), 'DELETE');
          refresh();
        };
        const watch = el('button', '', 'Watch');
        watch.className = 'primary';
        watch.onclick = () => window.ptWatchVideo(f.name);
        line.append(dot, name, state);
        if (f.live) line.append(watch);
        if (!f.source) line.append(mode);
        line.append(del);
        line.style.flexWrap = 'wrap';
        // Side by side on phones too (the dashboard makes buttons full width there).
        [watch, mode, del].forEach(b => { b.style.setProperty('width', 'auto', 'important'); b.style.padding = '6px 12px'; });
        list.appendChild(line);
      });
      droneSettings(info, feeds.filter(f => !f.udp_port && !f.source));
      icuSettings(info, feeds);
      bridgeHelp(info);
    }

    add.onclick = async () => {
      const name = input.value.trim().toLowerCase();
      if (!NAME.test(name)) {
        status.textContent = 'Use lowercase letters, numbers and dashes only (e.g. skydio1).';
        return;
      }
      const body = {name, udp: kind.value === 'udp'};
      if (kind.value === 'pull') {
        body.source = url.value.trim();
        if (!/^(rtsps?|rtmps?|srt|https?):\/\/\S+$/.test(body.source)) {
          status.textContent = 'Enter the camera or stream URL (rtsp://, rtsps://, rtmp://, srt:// or http(s)://).';
          return;
        }
      }
      add.disabled = true; status.textContent = 'Adding…';
      const r = await call('', 'POST', body);
      add.disabled = false;
      if (r.ok) {
        status.textContent = 'Added "' + name + '"' + (r.data.udp_port ? ' for TAK ICU (settings below).' : '.');
        input.value = '';
        if (r.data.udp_port) icu.open = true; else if (!r.data.source) drone.open = true;
        url.value = ''; kind.value = 'push'; kind.onchange();
      } else {
        const why = {invalid_feed_name: 'That name cannot be used.', too_many_feeds: 'Too many feeds (16 at most).',
                     invalid_source_url: 'That URL cannot be used (no spaces or quotes).',
                     video_server_not_running: 'Saved; it takes effect at the next start.'};
        status.textContent = why[r.data.detail] || ('Could not add the feed: ' + (r.data.detail || 'unknown error'));
      }
      refresh();
    };
    details.addEventListener('toggle', () => { if (details.open) refresh(); });

    // Directly after Live Feeds (or Users): the live data cards together.
    const headings = Array.from(document.querySelectorAll('section > h2, section summary'));
    const heading = headings.find(h => h.textContent.trim() === 'Live Feeds') || headings.find(h => h.textContent.trim() === 'Users');
    const after = heading && heading.closest('section');
    if (after) after.after(card); else (document.querySelector('main') || document.body).appendChild(card);
  }
  const go = () => setTimeout(build, 10);   // after the Live Feeds card, before ATAK Plugins
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();

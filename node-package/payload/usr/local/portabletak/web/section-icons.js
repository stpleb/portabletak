// Section icons: a line icon in front of each dashboard section title (the style of
// OpenTAKServer's web UI). Cards built later by the other scripts get theirs as they appear.
(function () {
  // 24x24 outline drawings (stroke only), keyed by section title.
  const ICONS = {
    'Server': '<rect x="4" y="4" width="16" height="6" rx="1.5"/><rect x="4" y="14" width="16" height="6" rx="1.5"/><path d="M8 7h.01M8 17h.01"/>',
    'Runtime Health': '<path d="M3 12h4l2-5 4 10 2-5h6"/>',
    'Network': '<circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M11 6.8 6 17.2M13 6.8l5 10.4M7 19h10"/>',
    'Service Health': '<path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
    'Identity': '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/><path d="M6 16c.6-1.4 1.7-2 3-2s2.4.6 3 2M14 10h4M14 14h3"/>',
    'Server Info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    'Controls': '<path d="M12 3v8"/><path d="M7 6.5a8 8 0 1 0 10 0"/>',
    'Remote Access': '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.5 5.6 3.5 9s-1 6.4-3.5 9c-2.5-2.6-3.5-5.6-3.5-9s1-6.4 3.5-9"/>',
    'Federation': '<rect x="2" y="4" width="8" height="6" rx="1"/><rect x="14" y="14" width="8" height="6" rx="1"/><path d="M6 10v4h8M18 14v-4h-8"/>',
    'Client Setup': '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
    'Users': '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M16 5.3a3 3 0 0 1 0 5.4M18 14.3c1.8.9 3 2.9 3 5.7"/>',
    'Channels': '<path d="M5 9h15M4 15h15M10 4 8 20M16 4l-2 16"/>',
    'Live Feeds': '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/><circle cx="12" cy="12" r=".6"/>',
    'Video Feeds': '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3z"/>',
    'ATAK Plugins': '<path d="M12 2.5 20 6v6c0 4.4-3.4 8-8 9.5C7.4 20 4 16.4 4 12V6z"/><path d="M6.5 9 12 11.5 17.5 9M8.5 7.2 12 8.8l3.5-1.6M9 15h6"/>',   // TAK badge
    'Meshtastic': '<path d="M3 18.5 9 6.5M10.5 18.5l5.25-11 5.25 11"/>',   // Meshtastic mark
    'Backup / Restore': '<path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 7v10c0 1.7 3.6 3 8 3M20 7v5"/><path d="M17 15v6M14.5 18.5 17 21l2.5-2.5"/>',
    'Activity': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'Erase Drive': '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  };
  const SELECTOR = 'h2, summary';   // only titles listed above get an icon

  function title(head) {
    // The heading's own text, without any icon or badge inside it.
    return Array.from(head.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
  }

  function decorate(root) {
    (root.querySelectorAll ? root : document).querySelectorAll(SELECTOR).forEach(head => {
      if (head.dataset.icon) return;
      const shape = ICONS[title(head)];
      if (!shape) return;
      head.dataset.icon = '1';
      const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      icon.setAttribute('viewBox', '0 0 24 24');
      icon.setAttribute('aria-hidden', 'true');
      icon.setAttribute('fill', 'none');
      icon.setAttribute('stroke', 'currentColor');
      icon.setAttribute('stroke-width', '1.8');
      icon.setAttribute('stroke-linecap', 'round');
      icon.setAttribute('stroke-linejoin', 'round');
      const color = title(head) === 'Erase Drive' ? 'var(--bad,#ef6b73)' : 'var(--accent,#63a7ff)';
      icon.style.cssText = 'width:1.1em;height:1.1em;vertical-align:-0.17em;margin-right:0.45em;flex:none;color:' + color;
      icon.innerHTML = shape;
      // After a <details> marker (the arrow), before the title text.
      const text = Array.from(head.childNodes).find(n => n.nodeType === 3 && n.textContent.trim());
      head.insertBefore(icon, text || head.firstChild);
    });
  }

  function start() {
    decorate(document);
    new MutationObserver(records => {
      if (records.some(r => r.addedNodes.length)) decorate(document);
    }).observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();

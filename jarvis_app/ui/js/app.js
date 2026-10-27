/* J.A.R.V.I.S. UI-Logik: Bridge-Glue, Chat-Panel, Orb-Interaktion.
 *
 * Python ruft (siehe bridge.py):
 *   jarvis.onToken(sid, text) · jarvis.onState(state, amplitude)
 *   jarvis.onStatus(status)   · jarvis.onMeta(sid, ev) · jarvis.onDaemon(connected)
 */
(function () {
  'use strict';

  var engine = null;
  var api = null;
  var busy = false;
  var force7b = false;
  var heavyAvailable = true;
  var lastOnline = null;
  var streams = {}; // sid → {textSpan, cursor, gotToken, el}

  var $ = function (id) { return document.getElementById(id); };
  var orbzone, log, input;
  var captionMsg = null; // laufende Jarvis-Nachricht aus Sprach-Captions

  // ------------------------------------------------------ Chat-Verlauf

  function scrollLog() {
    log.scrollTop = log.scrollHeight;
  }

  function addMsg(who, cls) {
    var el = document.createElement('div');
    el.className = 'msg ' + cls;
    var whoEl = document.createElement('div');
    whoEl.className = 'who';
    whoEl.textContent = who;
    var text = document.createElement('div');
    text.className = 'text';
    el.appendChild(whoEl);
    el.appendChild(text);
    log.appendChild(el);
    scrollLog();
    return { el: el, text: text };
  }

  function addUserMsg(content) {
    var m = addMsg('SIR', 'user');
    m.text.textContent = content;
  }

  function esc(t) {
    var e = document.createElement('div');
    e.textContent = t == null ? '' : t;
    return e.innerHTML;
  }

  function card(title, rows) {
    var c = document.createElement('div');
    c.className = 'card';
    var h = document.createElement('h3');
    h.textContent = title;
    c.appendChild(h);
    rows.forEach(function (r) {
      var x = document.createElement('div');
      x.className = 'row';
      x.innerHTML = r;
      c.appendChild(x);
    });
    return c;
  }

  function startStream(sid) {
    var m = addMsg('J.A.R.V.I.S.', 'jarvis');
    var t = document.createElement('span');
    t.className = 't';
    var cur = document.createElement('span');
    cur.className = 'cursor';
    m.text.appendChild(t);
    m.text.appendChild(cur);
    streams[sid] = { el: m.el, textSpan: t, cursor: cur, gotToken: false };
    return streams[sid];
  }

  function finishStream(sid) {
    var s = streams[sid];
    if (s && s.cursor && s.cursor.parentNode) s.cursor.parentNode.removeChild(s.cursor);
    setBusy(false);
  }

  function addSystemLine(text) {
    var m = addMsg('J.A.R.V.I.S.', 'jarvis');
    m.text.textContent = text;
  }

  function setBusy(b) {
    busy = b;
    input.disabled = b;
    $('btn-7b').disabled = b;
    if (!b) input.focus();
  }

  // ----------------------------------------------------- Bridge → UI

  window.jarvis = {
    onToken: function (sid, chunk) {
      var s = streams[sid] || startStream(sid);
      s.gotToken = true;
      s.textSpan.textContent += chunk;
      scrollLog();
    },
    onState: function (state, amplitude) {
      if (engine) engine.setState(state, amplitude);
    },
    onStatus: function (st) {
      $('st-model').textContent = st.model || '—';
      $('st-tps').textContent = st.tps ? st.tps.toFixed(0) : '—';
      $('st-cpu').textContent = st.cpu !== null && st.cpu !== undefined ? st.cpu + '%' : '—';
      $('st-ram').textContent = st.ram !== null && st.ram !== undefined ? st.ram + '%' : '—';
      $('st-vram').textContent = st.vram !== null && st.vram !== undefined ? st.vram + '%' : '—';
      $('st-heavy').textContent = st.heavy || '—';
      $('st-heavy-wrap').className = st.heavy === 'AKTIV' ? 'heavy-active' : '';

      if (st.online === false && lastOnline !== false) {
        $('panel-online').innerHTML = 'OFFLINE <span class="blink">▮</span>';
        $('panel-online').classList.add('offline');
        if (engine) { engine.setOffline(true); engine.setState('idle'); }
        addSystemLine('Verbindung zum Mainframe unterbrochen, Sir. Ich versuche es weiter.');
      } else if (st.online === true && lastOnline === false) {
        $('panel-online').innerHTML = 'ONLINE <span class="blink">▮</span>';
        $('panel-online').classList.remove('offline');
        if (engine) engine.setOffline(false);
        addSystemLine('Verbindung wiederhergestellt, Sir.');
      } else if (st.online === true && engine && engine.offline) {
        engine.setOffline(false);
      }
      if (st.online !== null && st.online !== undefined) lastOnline = st.online;
    },
    onMeta: function (sid, ev) {
      switch (ev.type) {
        case 'route':
          startStream(sid);
          break;
        case 'done':
          finishStream(sid);
          break;
        case 'error':
          var s = streams[sid];
          if (s && s.gotToken) {
            var err = document.createElement('div');
            err.className = 'err';
            err.textContent = ev.message;
            s.el.appendChild(err);
          } else if (s) {
            s.textSpan.textContent = ev.message;
          } else {
            addSystemLine(ev.message);
          }
          finishStream(sid);
          scrollLog();
          break;
      }
    },
    onDaemon: function (connected) {
      $('btn-mic').disabled = !connected;
    },
    onUserMessage: function (text) {
      addUserMsg(text);
      captionMsg = null; // neue Sprachfrage → nächste Caption beginnt frische Antwort
    },
    onCaption: function (text) {
      // Sprach-Antwort: jede Sentence erscheint genau dann, wenn Jarvis sie spricht
      if (!captionMsg) captionMsg = addMsg('J.A.R.V.I.S.', 'jarvis');
      captionMsg.text.textContent +=
        (captionMsg.text.textContent ? ' ' : '') + text;
      scrollLog();
    },
    onBriefing: function (data) {
      var d = $('dashboard');
      d.innerHTML = '';
      d.appendChild(card('LAGE', [esc(data.datetime || '')]));
      var w = data.weather;
      d.appendChild(card('WETTER', w
        ? ['<span class="big">' + w.temp_now + '°</span> ' + esc(w.text || '') +
           ' · max ' + w.temp_max + '° / min ' + w.temp_min + '° · ' + esc(w.ort || '')]
        : ['— nicht verfügbar —']));
      var mails = data.mails;
      d.appendChild(card('GMAIL' + (mails ? ' (' + mails.length + ')' : ''),
        mails === null ? ['— nicht verfügbar —'] :
        mails.length ? mails.map(function (m) { return '<b>' + esc(m.sender) + ':</b> ' + esc(m.subject); })
                     : ['Keine neuen Nachrichten']));
      var iss = data.issues;
      d.appendChild(card('GITHUB · ' + esc(data.repo || ''),
        iss === null ? ['— nicht verfügbar —'] :
        iss.length ? iss.map(function (i) { return '<b>#' + i.number + '</b> ' + esc(i.title); })
                   : ['Keine offenen Punkte']));
      var s = data.system || {};
      d.appendChild(card('SYSTEM', ['CPU <b>' + (s.cpu != null ? s.cpu + '%' : '—') +
        '</b> · RAM <b>' + (s.ram != null ? s.ram + '%' : '—') + '</b> · VRAM <b>' +
        (s.vram != null ? s.vram + '%' : '—') + '</b>']));
      $('log').classList.add('hidden');
      d.classList.remove('hidden');
    }
  };

  // ------------------------------------------------------- UI → Bridge

  function send() {
    var text = input.value.trim();
    if (!text || busy || !api) return;
    addUserMsg(text);
    input.value = '';
    setBusy(true);
    var f = force7b;
    setForce7b(false); // gilt nur für die nächste Anfrage
    api.send_message(text, f);
  }

  function setForce7b(v) {
    force7b = v && heavyAvailable;
    $('btn-7b').classList.toggle('latched', force7b);
  }

  function togglePanel() {
    if (!api) return;
    api.toggle_panel().then(function (res) {
      var panel = $('panel');
      var stage = $('stage');
      panel.classList.toggle('hidden', !res.open);
      stage.className = 'side-' + (res.side || 'right');
      if (res.open) {
        if (!log.children.length) addSystemLine('Online und bereit, Sir.');
        input.focus();
      }
    });
  }

  // --------------------------------------------------- Orb-Interaktion

  function bindOrb() {
    var down = null;
    orbzone.addEventListener('mousedown', function (e) {
      if (e.button === 0) down = { x: e.screenX, y: e.screenY, t: Date.now() };
    });
    orbzone.addEventListener('mouseup', function (e) {
      if (!down || e.button !== 0) return;
      var moved = Math.hypot(e.screenX - down.x, e.screenY - down.y);
      var dt = Date.now() - down.t;
      down = null;
      if (moved < 6 && dt < 600) togglePanel();
    });
    orbzone.addEventListener('wheel', function (e) {
      e.preventDefault();
      if (!api) return;
      api.resize_orb(e.deltaY < 0 ? 1 : -1).then(function (res) {
        applyZone(res.zone);
      });
    }, { passive: false });

    orbzone.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      var menu = $('ctxmenu');
      menu.classList.remove('hidden');
      menu.style.left = Math.min(e.clientX, window.innerWidth - 180) + 'px';
      menu.style.top = Math.min(e.clientY, window.innerHeight - 110) + 'px';
    });
    document.addEventListener('mousedown', function (e) {
      var menu = $('ctxmenu');
      if (!menu.classList.contains('hidden') && !menu.contains(e.target)) {
        menu.classList.add('hidden');
      }
    });
    $('ctxmenu').addEventListener('click', function (e) {
      var act = e.target.getAttribute('data-act');
      if (!act) return;
      $('ctxmenu').classList.add('hidden');
      if (act === 'panel') togglePanel();
      if (act === 'reset' && api) {
        api.reset_chat().then(function () {
          log.innerHTML = '';
          addSystemLine('Verlauf gelöscht, Sir. Ein frisches Blatt.');
        });
      }
      if (act === 'quit' && api) api.quit_app();
    });
  }

  function applyZone(zone) {
    orbzone.style.width = zone + 'px';
    orbzone.style.height = zone + 'px';
    if (engine) engine.resize(zone);
  }

  // ------------------------------------------------------------- Start

  function init() {
    api = window.pywebview.api;
    api.get_config().then(function (cfg) {
      var o = cfg.orb || {};
      heavyAvailable = !!cfg.heavy_available;
      if (!heavyAvailable) $('btn-7b').classList.add('unavailable');
      if (cfg.window_bg === 'dark') document.body.classList.add('bg-dark');

      applyZone(cfg.zone);
      engine = new window.JarvisOrb($('orb'), {
        seed: o.seed, sparks: o.sparks, veins: o.veins, rays: o.rays,
        ringb: o.ring_brightness, bloom: o.bloom, speed: o.speed,
        panels: o.panels, arcs: o.arcs, backdrop: o.backdrop,
        transparent: true
      });
      engine.resize(cfg.zone);
      engine.setState('idle');
      return api.get_status();
    }).then(function (st) {
      if (st) window.jarvis.onStatus(st);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    orbzone = $('orbzone');
    log = $('log');
    input = $('input');

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') send();
      if (e.key === 'Escape') togglePanel();
    });
    $('btn-7b').addEventListener('click', function () { setForce7b(!force7b); });
    $('btn-mic').addEventListener('click', function () { if (api) api.push_to_talk(); });
    $('btn-briefing').addEventListener('click', function () { if (api) api.request_briefing(); });
    input.addEventListener('focus', function () {
      $('dashboard').classList.add('hidden');
      $('log').classList.remove('hidden');
    });

    bindOrb();

    if (window.pywebview && window.pywebview.api) init();
    else window.addEventListener('pywebviewready', init);
  });
})();

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
  var muted = false;
  var daemonUp = false;
  var ctxWarned = false;
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

  // ---- Schreibmaschine: Text erscheint Zeichen für Zeichen, als würde er gerade getippt ----
  // typer = {el, q, cps, mark, timer, last, onIdle}. Mit Dauer (Sprache): endet pünktlich zum Satzende.
  // Ohne Dauer (getippte Antwort): ~70 Zeichen/s, bei Rückstand schneller, damit nichts hinterherhinkt.
  function makeTyper(el, cursor) {
    return { el: el, cursor: cursor || null, q: '', cps: 70, acc: 0, last: 0, timer: null, onIdle: null };
  }

  function typerPush(t, text, seconds) {
    if (!text) return;
    t.q += text;
    if (seconds && seconds > 0.2) {
      // gesprochen: Satz soll kurz vor Ende der Stimme fertig getippt sein (Rückstand mitgerechnet)
      t.cps = Math.max(18, t.q.length / (seconds * 0.9));
      t.fixed = true;
    } else {
      t.fixed = false;
    }
    if (!t.timer) {
      t.last = performance.now();
      t.timer = setInterval(function () { typerTick(t); }, 16);
    }
  }

  function typerTick(t) {
    var now = performance.now();
    var dt = (now - t.last) / 1000;
    t.last = now;
    var cps = t.cps;
    if (!t.fixed) cps = Math.max(70, t.q.length * 2.2);          // Aufholen bei Rückstand
    t.acc += cps * dt;
    var n = Math.floor(t.acc);
    if (n > 0) {
      t.acc -= n;
      var chunk = t.q.slice(0, n);
      t.q = t.q.slice(n);
      t.el.appendChild(document.createTextNode(chunk));
      scrollLog();
    }
    if (!t.q.length) {
      clearInterval(t.timer);
      t.timer = null;
      if (t.onIdle) t.onIdle();
    }
  }

  function typerFlush(t) {
    if (t.q.length) { t.el.appendChild(document.createTextNode(t.q)); t.q = ''; }
    if (t.timer) { clearInterval(t.timer); t.timer = null; }
  }

  function startStream(sid) {
    var m = addMsg('J.A.R.V.I.S.', 'jarvis');
    var t = document.createElement('span');
    t.className = 't';
    var cur = document.createElement('span');
    cur.className = 'cursor';
    m.text.appendChild(t);
    m.text.appendChild(cur);
    streams[sid] = { el: m.el, textSpan: t, cursor: cur, gotToken: false, done: false };
    streams[sid].typer = makeTyper(t, cur);
    return streams[sid];
  }

  function finishStream(sid) {
    var s = streams[sid];
    function finalize() {
      if (s && s.cursor && s.cursor.parentNode) s.cursor.parentNode.removeChild(s.cursor);
      setBusy(false);
    }
    if (s && s.typer && s.typer.q.length) {      // erst fertig tippen, dann Cursor weg und Eingabe frei
      s.typer.onIdle = finalize;
    } else {
      finalize();
    }
  }

  function addSystemLine(text) {
    var m = addMsg('J.A.R.V.I.S.', 'jarvis');
    m.text.textContent = text;
  }

  function setBusy(b) {
    busy = b;
    input.disabled = b;
    $('btn-7b').disabled = b;
    $('btn-stop').classList.toggle('hidden', !b);
    if (!b) input.focus();
  }

  // Aktionszeile eines Werkzeugs („▸ Ich lege den Ordner an“) bzw. Ergebnis (✓/✗)
  function addActionLine(sid, text, cls) {
    var s = streams[sid] || startStream(sid);
    var line = document.createElement('div');
    line.className = 'act ' + (cls || '');
    line.textContent = text;
    s.el.insertBefore(line, s.el.querySelector('.text'));
    scrollLog();
  }

  // Bestätigungskarte: zeigt die konkrete Aktion; die Zustimmung gilt genau dieser id
  function addConfirm(ev) {
    var c = document.createElement('div');
    c.className = 'confirm';
    var h = document.createElement('div');
    h.className = 'confirm-title';
    h.textContent = 'BESTÄTIGUNG ERFORDERLICH';
    var body = document.createElement('pre');
    body.className = 'confirm-body';
    body.textContent = ev.summary;
    var row = document.createElement('div');
    row.className = 'confirm-row';
    var yes = document.createElement('button');
    yes.className = 'ctl-btn'; yes.textContent = 'AUSFÜHREN';
    var no = document.createElement('button');
    no.className = 'ctl-btn'; no.textContent = 'ABLEHNEN';
    function decide(ok) {
      yes.disabled = no.disabled = true;
      c.classList.add(ok ? 'approved' : 'declined');
      h.textContent = ok ? 'BESTÄTIGT' : 'ABGELEHNT';
      if (api) api.confirm_action(ev.id, ok);
    }
    yes.addEventListener('click', function () { decide(true); });
    no.addEventListener('click', function () { decide(false); });
    row.appendChild(yes); row.appendChild(no);
    c.appendChild(h); c.appendChild(body); c.appendChild(row);
    log.appendChild(c);
    scrollLog();
  }

  function fmtTok(n) { return n >= 1000 ? (n / 1000).toFixed(1).replace('.0', '') + 'k' : String(n); }

  // Kontextfenster: belegte Tokens / Größe, Balken wird gelb ab 70 %, rot ab 90 %
  function showContext(c) {
    var pct = c.pct || 0;
    $('st-ctx').textContent = fmtTok(c.used) + '/' + fmtTok(c.total) + ' · ' + pct + '%';
    $('st-ctx-wrap').title = c.messages + ' Nachrichten im Verlauf · ' + c.source + ' · ' + (c.model || '');
    $('ctxfill').style.width = Math.min(100, pct) + '%';
    var cls = pct >= 90 ? 'full' : pct >= 70 ? 'warn' : '';
    $('ctxfill').className = cls;
    $('st-ctx-wrap').className = cls;
    if (pct >= 85 && !ctxWarned) {
      ctxWarned = true;
      var n = document.createElement('div');
      n.className = 'ctx-note';
      n.textContent = 'Das Kontextfenster ist fast voll, Sir — für bessere Antworten einen neuen Chat starten (NEU).';
      log.appendChild(n); scrollLog();
    }
    if (pct < 50) ctxWarned = false;
  }

  function setMuteUi(m) {
    muted = m;
    $('btn-mute').classList.toggle('muted', m);
    $('btn-mute').title = m ? 'Mikrofon ist AUS — Klick schaltet Jarvis wieder zum Zuhören' :
                              'Mikrofon stumm schalten — Jarvis hört dann nicht mehr zu';
    $('btn-mic').disabled = m || !daemonUp;
    if (m && engine) engine.setState('idle');
  }

  // Laufende (parallele) Aufgaben: was macht Jarvis gerade — jede einzeln abbrechbar
  function showTasks(list) {
    var box = $('tasks');
    box.innerHTML = '';
    (list || []).forEach(function (t) {
      var row = document.createElement('div');
      row.className = 'trow' + (t.status === 'confirm' ? ' confirm' : '');
      var dot = document.createElement('span'); dot.className = 'pulse';
      var id = document.createElement('b'); id.textContent = '#' + t.id;
      var txt = document.createElement('span'); txt.className = 'tcur';
      txt.textContent = t.text + ' — ' + t.current + ' (' + t.seconds + ' s)';
      var x = document.createElement('button'); x.className = 'tx'; x.textContent = '✕';
      x.title = 'Diese Aufgabe abbrechen';
      x.addEventListener('click', function () { if (api) api.cancel_task(t.id); });
      row.appendChild(dot); row.appendChild(id); row.appendChild(txt); row.appendChild(x);
      box.appendChild(row);
    });
    box.classList.toggle('hidden', !(list && list.length));
  }

  function clearChat() {
    log.innerHTML = '';
    $('dashboard').classList.add('hidden');
    $('log').classList.remove('hidden');
    Object.keys(streams).forEach(function (k) { if (streams[k].typer) typerFlush(streams[k].typer); });
    if (captionMsg && captionMsg.typer) typerFlush(captionMsg.typer);
    streams = {}; captionMsg = null; ctxWarned = false;
    setBusy(false);
    addSystemLine('Neuer Chat, Sir. Der Verlauf ist leer.');
  }

  // ----------------------------------------------------- Bridge → UI

  window.jarvis = {
    onToken: function (sid, chunk) {
      var s = streams[sid] || startStream(sid);
      s.gotToken = true;
      typerPush(s.typer, chunk, null);
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

      if (st.context) showContext(st.context);
      if (st.tasks) showTasks(st.tasks);
      if (st.muted !== undefined && st.muted !== muted) setMuteUi(!!st.muted);
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
        case 'reasoning':
          if (ev.active) {
            var rs = streams[sid] || startStream(sid);
            if (!rs.reasoningLine) {
              rs.reasoningLine = document.createElement('div');
              rs.reasoningLine.className = 'act';
              rs.reasoningLine.textContent = 'Jarvis denkt nach …';
              rs.el.insertBefore(rs.reasoningLine, rs.el.querySelector('.text'));
              scrollLog();
            }
          }
          break;
        case 'done':
          if (streams[sid] && streams[sid].reasoningLine) streams[sid].reasoningLine.remove();
          finishStream(sid);
          break;
        case 'error':
          var s = streams[sid];
          if (s && s.reasoningLine) s.reasoningLine.remove();
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
    onAction: function (sid, text, ok) {
      addActionLine(sid, (ok === false ? '✗ ' : ok === true ? '✓ ' : '▸ ') + text,
                    ok === false ? 'bad' : ok === true ? 'good' : '');
    },
    onConfirm: function (ev) { addConfirm(ev); },
    onNotice: function (text) { addSystemLine(text); },
    onNewChat: function () { clearChat(); },
    onTasks: function (list) { showTasks(list); },
    onDaemon: function (connected) {
      daemonUp = !!connected;
      $('btn-mic').disabled = !daemonUp || muted;
    },
    onUserMessage: function (text) {
      addUserMsg(text);
      captionMsg = null; // neue Sprachfrage → nächste Caption beginnt frische Antwort
    },
    onCaption: function (text, seconds) {
      // Sprach-Antwort: jede Sentence wird genau dann getippt, wenn Jarvis sie spricht
      if (!captionMsg) {
        captionMsg = addMsg('J.A.R.V.I.S.', 'jarvis');
        var t = document.createElement('span'); t.className = 't';
        var cur = document.createElement('span'); cur.className = 'cursor';
        captionMsg.text.appendChild(t); captionMsg.text.appendChild(cur);
        captionMsg.typer = makeTyper(t, cur);
        captionMsg.typer.onIdle = function () {              // Cursor nach kurzer Ruhe ausblenden
          clearTimeout(captionMsg && captionMsg.hide);
          var m = captionMsg;
          if (m) m.hide = setTimeout(function () { if (m.typer && !m.typer.q.length && cur.parentNode) cur.parentNode.removeChild(cur); }, 1800);
        };
      }
      var sep = captionMsg.typer.el.textContent.length || captionMsg.typer.q.length ? ' ' : '';
      if (captionMsg.hide) clearTimeout(captionMsg.hide);
      if (!captionMsg.text.contains(captionMsg.typer.cursor)) captionMsg.text.appendChild(captionMsg.typer.cursor);
      typerPush(captionMsg.typer, sep + text, seconds);
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
        api.new_chat().then(function (r) { clearChat(); if (r && r.context) showContext(r.context); });
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
    $('btn-newchat').addEventListener('click', function () {
      if (!api) return;
      api.new_chat().then(function (r) { clearChat(); if (r && r.context) showContext(r.context); });
    });
    $('btn-mute').addEventListener('click', function () {
      if (!api) return;
      api.set_mute(!muted).then(function (r) {
        setMuteUi(!!r.muted);
        if (r.muted && !r.daemon) addSystemLine('Stumm gemerkt, Sir — gilt, sobald der Sprach-Daemon verbunden ist.');
      });
    });
    $('btn-stop').addEventListener('click', function () { if (api) api.cancel(); });
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

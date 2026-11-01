/* Cinematic startup HUD driven by actual backend milestones. No simulated readiness. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function Startup(api, mainOrb) {
    this.api = api;
    this.mainOrb = mainOrb;
    this.hud = null;
    this.orb = null;
    this.orbObserver = null;
    this.stepRows = new Map();
    this.timer = null;
    this.key = null;
    this.sleeping = false;
    this.startedAt = 0;
    this.mode = 'wake';
    this.wakeTimer = null;
    this.overlay = document.createElement('section');
    this.overlay.id = 'startup-overlay';
    this.overlay.className = 'hidden';
    this.overlay.setAttribute('aria-label', 'Jarvis Startsequenz');
    this.overlay.innerHTML =
      '<div class="startup-grid" aria-hidden="true"></div><div class="startup-scan" aria-hidden="true"></div>' +
      '<i class="hud-corner hud-tl" aria-hidden="true"></i><i class="hud-corner hud-tr" aria-hidden="true"></i>' +
      '<i class="hud-corner hud-bl" aria-hidden="true"></i><i class="hud-corner hud-br" aria-hidden="true"></i>' +
      '<header class="startup-head"><span class="startup-brand">PERSONAL SYSTEM / J.A.R.V.I.S.</span>' +
      '<span class="startup-live"><i></i> LOKALE VERBINDUNG</span></header>' +
      '<div class="startup-heading"><div id="startup-kicker">SYSTEM INITIALIZATION</div>' +
      '<h1>J.A.R.V.I.S.</h1><p id="startup-title">Willkommen zurück, Sir.</p></div>' +
      '<div class="startup-stage"><canvas id="startup-hud" aria-hidden="true"></canvas>' +
      '<div class="startup-orb-shell"><canvas id="startup-orb" aria-hidden="true"></canvas></div>' +
      '<aside class="hud-wing hud-wing-left"><span class="hud-label">PROTOKOLL</span>' +
      '<b id="startup-mode">SYSTEMSTART</b><div class="hud-rule"></div>' +
      '<span class="hud-label">STARTPRÜFUNGEN</span><strong id="startup-count">00 / 00</strong>' +
      '<span class="hud-small">ABGESCHLOSSEN</span></aside>' +
      '<aside class="hud-wing hud-wing-right"><span class="hud-label">SPRACHVERBINDUNG</span>' +
      '<b id="startup-voice">WARTET</b><div class="hud-rule"></div>' +
      '<span class="hud-label">SOUNDTRACK</span><b id="startup-audio">DATEI WÄHLEN</b>' +
      '<span class="hud-small">LOKALE AUDIODATEI</span></aside>' +
      '<span id="startup-reactor-label" class="reactor-caption">INITIALISIERUNG</span></div>' +
      '<section class="startup-console" aria-label="Ergebnisse der Startprüfungen">' +
      '<div class="startup-progress-head"><span id="startup-detail">VERBINDUNG ZUR OBERFLÄCHE …</span>' +
      '<b id="startup-percent">0<span>%</span></b></div>' +
      '<div id="startup-progress" role="progressbar" aria-label="Abgeschlossene Startprüfungen" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div></div></div>' +
      '<div id="startup-steps" aria-live="polite"></div>' +
      '<div id="startup-footer">LOKALE SYSTEMPRÜFUNG</div></section>' +
      '<footer class="startup-foot"><span id="startup-music">SOUNDTRACK / DATEI WÄHLEN</span>' +
      '<div class="startup-actions"><button class="ctl-btn" id="startup-replay" hidden>Noch einmal</button>' +
      '<button class="ctl-btn" id="startup-close">Im Hintergrund</button>' +
      '<button class="ctl-btn" id="startup-cancel">Abbrechen</button></div></footer>';
    document.body.appendChild(this.overlay);
    if (window.JARVIS_PREVIEW) this.overlay.querySelector('.startup-brand').textContent = 'VORSCHAU / DEMODATEN';

    var controls = document.createElement('div');
    controls.id = 'experience-controls';
    controls.innerHTML = '<button class="ctl-btn" data-experience="wake">AUFWACHEN</button>' +
      '<button class="ctl-btn" data-experience="sleep">STANDBY</button>' +
      '<button class="ctl-btn" data-experience="reboot" title="Nur Jarvis neu starten">NEUSTART</button>' +
      '<button class="ctl-btn" id="choose-music">♫ MUSIK WÄHLEN</button>' +
      '<button class="ctl-btn hidden" id="stop-music" title="Hintergrundmusik stoppen">♫ ■</button>';
    $('statusbar').insertAdjacentElement('afterend', controls);
    var status = document.createElement('button');
    status.id = 'experience-chip';
    status.className = 'hidden';
    $('orbzone').appendChild(status);
    this.chip = status;
    var self = this;
    controls.querySelectorAll('[data-experience]').forEach(function (button) {
      button.addEventListener('click', function () { self.run(button.dataset.experience); });
    });
    $('choose-music').addEventListener('click', function () {
      api.choose_music().then(function (r) {
        if (!r.cancelled) window.jarvis.onNotice(r.ok ? 'Soundtrack gewählt: ' + r.title : r.message);
      }).catch(function () { window.jarvis.onNotice('Die Dateiauswahl ist gerade nicht verfügbar.'); });
    });
    $('stop-music').addEventListener('click', function () { self.run('music_stop'); });
    $('startup-close').addEventListener('click', function () { self.hide(); });
    $('startup-replay').addEventListener('click', function () { self.run(self.mode === 'boot' ? 'boot' : 'wake'); });
    $('startup-cancel').addEventListener('click', function () { api.cancel(); });
    this.chip.addEventListener('mousedown', function (e) { e.stopPropagation(); });
    this.chip.addEventListener('mouseup', function (e) { e.stopPropagation(); });
    this.chip.addEventListener('click', function (e) {
      e.stopPropagation();
      if (self.sleeping) self.run('wake');
      else self.show();
    });
  }

  Startup.prototype.run = function (mode) {
    this.api.request_experience(mode).then(function (r) {
      if (!r.ok) window.jarvis.onNotice(r.message);
    }).catch(function () { window.jarvis.onNotice('Startbefehl konnte nicht ausgeführt werden.'); });
  };

  Startup.prototype.show = function () {
    // Commit the hidden state so the staged interface reveal also restarts on replay.
    if (this.overlay.classList.contains('hidden')) void this.overlay.offsetWidth;
    this.overlay.classList.remove('hidden');
    if (!this.hud) {
      this.hud = new window.JarvisStartupHUD($('startup-hud'), reduced);
      if (this.lastState) this.hud.setState(this.lastState);
    }
    if (!this.orb) {
      // Preserve the user's exact orb geometry, colours, seed and rendering parameters.
      var params = Object.assign({}, this.mainOrb.P);
      if (reduced) params.speed = 0;
      this.orb = new window.JarvisOrb($('startup-orb'), params);
      this.orb.setState(this.shutdown ? 'sleep' : this.lastState && this.lastState.phase === 'ready' ? 'idle' : 'boot');
      var self = this;
      var fitOrb = function () {
        var stage = self.overlay.querySelector('.startup-stage').getBoundingClientRect();
        if (self.orb) self.orb.resize(Math.max(100, Math.min(stage.height * 0.98, stage.width * 0.7)));
      };
      this.orbObserver = new ResizeObserver(fitOrb);
      this.orbObserver.observe(this.overlay.querySelector('.startup-stage'));
      fitOrb();
    }
  };

  Startup.prototype.hide = function () {
    this.overlay.classList.add('hidden');
    if (this.hud) { this.hud.destroy(); this.hud = null; }
    if (this.orb) { this.orb.destroy(); this.orb = null; }
    if (this.orbObserver) { this.orbObserver.disconnect(); this.orbObserver = null; }
  };

  Startup.prototype.music = function (m) {
    if (!m) return;
    $('choose-music').textContent = m.configured ? '♫ MUSIK ÄNDERN' : '♫ MUSIK WÄHLEN';
    $('choose-music').title = m.file || 'Lokale AC/DC-Audiodatei auswählen';
    $('stop-music').classList.toggle('hidden', !m.playing);
    $('startup-audio').textContent = m.error ? 'NICHT VERFÜGBAR' : m.playing ? 'WIEDERGABE' : m.configured ? 'BEREIT' : 'DATEI WÄHLEN';
    $('startup-music').textContent = m.error ? 'SOUNDTRACK / NICHT VERFÜGBAR' :
      m.playing ? '♫ ' + (m.title || m.file) : m.configured ? 'SOUNDTRACK / ' + m.file : 'SOUNDTRACK / DATEI WÄHLEN';
  };

  Startup.prototype.update = function (state) {
    this.lastState = state;
    this.music(state.music);
    this.sleeping = state.mode === 'sleep';
    var body = document.body;
    body.classList.toggle('jarvis-standby', this.sleeping);
    // Einschlafen: Panel fährt animiert herunter, bevor das Fenster auf den Orb schrumpft
    body.classList.toggle('jarvis-going-sleep', this.sleeping && state.phase === 'sleeping');
    if (state.mode === 'wake') {
      // Aufwachen ist KEIN Systemstart: keine Prüfliste, kein Briefing — nur die Weckanimation
      clearTimeout(this.timer);
      this.timer = null;
      this.hide();
      this.chip.classList.add('hidden');
      body.classList.add('jarvis-waking');
      clearTimeout(this.wakeTimer);
      if (state.phase === 'awake') {
        this.mainOrb.setState('idle');
        this.wakeTimer = setTimeout(function () { body.classList.remove('jarvis-waking'); }, 1000);
      } else {
        this.mainOrb.setState('boot');
      }
      return;
    }
    if (this.sleeping) {
      clearTimeout(this.timer);
      this.timer = null;
      this.hide();
      body.classList.remove('jarvis-waking');
      this.mainOrb.setState('sleep');
      this.chip.classList.remove('hidden');
      this.chip.textContent = 'STANDBY · AUFWACHEN';
      this.chip.title = '„Hey Jarvis“, dann „wake up“ — oder klicken';
      return;
    }
    if (state.phase === 'idle') return;
    if (state.phase === 'cancelled') {
      clearTimeout(this.timer);
      this.timer = null;
      this.hide(); this.chip.classList.add('hidden'); this.mainOrb.setState('idle');
      return;
    }
    var key = state.mode + ':' + state.run_id;
    this.mode = state.mode;
    this.shutdown = state.phase === 'shutdown';
    if (key !== this.key) {
      clearTimeout(this.timer);
      this.timer = null;
      this.key = key;
      this.startedAt = performance.now();
      this.hide();
      this.overlay.classList.remove('startup-ready', 'shutting-down');
      this.stepRows.clear();
      $('startup-steps').replaceChildren();
      this.show();
      this.mainOrb.setState('boot');
    }
    this.overlay.classList.toggle('shutting-down', this.shutdown);
    var ready = state.phase === 'ready';
    this.overlay.classList.toggle('startup-ready', ready);
    if (this.hud) this.hud.setState(state);
    if (this.orb) this.orb.setState(this.shutdown ? 'sleep' : ready ? 'idle' : 'boot');
    $('startup-mode').textContent = this.shutdown ? 'NEUSTART' : state.mode === 'wake' ? 'AUFWACHEN' : 'SYSTEMSTART';
    $('startup-kicker').textContent = this.shutdown ? 'SYSTEM REBOOT' : state.mode === 'wake' ? 'WAKE-UP PROTOCOL' : 'SYSTEM INITIALIZATION';
    $('startup-title').textContent = this.shutdown ? 'Bis gleich, Sir.' : ready ?
      (state.warnings ? 'Für Sie da. Mit Einschränkungen.' : 'Für Sie bereit, Sir.') :
      state.mode === 'wake' ? 'Zeit, aufzuwachen, Sir.' : 'Willkommen zurück, Sir.';
    $('startup-reactor-label').textContent = this.shutdown ? 'SYSTEM WIRD BEENDET' : ready ?
      (state.warnings ? 'SYSTEM BEREIT · HINWEISE BEACHTEN' : 'SYSTEM BEREIT') : 'KERN WIRD INITIALISIERT';
    $('startup-footer').textContent = this.shutdown ? 'NUR JARVIS WIRD NEU GESTARTET' : ready ?
      (state.warnings ? state.warnings + ' HINWEIS(E) · DETAILS OBEN' : 'STARTPRÜFUNG ABGESCHLOSSEN') : 'LOKALE SYSTEMPRÜFUNG';
    var steps = $('startup-steps');
    var self = this;
    var visibleIds = new Set();
    (state.steps || []).forEach(function (step, i) {
      var id = String(step.id);
      visibleIds.add(id);
      var row = self.stepRows.get(id);
      if (!row) {
        row = document.createElement('div');
        row.innerHTML = '<span class="step-number"></span><div><b></b><span class="step-detail"></span></div><span class="step-mark"></span>';
        row.style.setProperty('--step-delay', Math.max(0, 1.8 + i * 0.14 - (performance.now() - self.startedAt) / 1000) + 's');
        self.stepRows.set(id, row);
        steps.appendChild(row);
      }
      row.className = 'startup-step is-' + step.state;
      row.title = step.detail || 'Wartet';
      row.querySelector('.step-number').textContent = String(i + 1).padStart(2, '0');
      row.querySelector('b').textContent = step.label;
      row.querySelector('.step-detail').textContent = step.detail || 'Wartet';
      row.querySelector('.step-mark').textContent = { ok: '✓', warn: '!', skip: '—', pending: '·', loading: '◌' }[step.state] || '·';
    });
    this.stepRows.forEach(function (row, id) {
      if (!visibleIds.has(id)) { row.remove(); self.stepRows.delete(id); }
    });
    var pct = Math.max(0, Math.min(100, state.progress || 0));
    $('startup-percent').textContent = this.shutdown ? 'REBOOT' : pct + '%';
    $('startup-progress').setAttribute('aria-valuenow', pct);
    $('startup-progress').firstElementChild.style.width = pct + '%';
    var done = (state.steps || []).filter(function (s) { return ['ok', 'skip', 'warn'].indexOf(s.state) >= 0; }).length;
    $('startup-count').textContent = String(done).padStart(2, '0') + ' / ' + String((state.steps || []).length).padStart(2, '0');
    var voice = (state.steps || []).find(function (s) { return s.id === 'voice' || s.label === 'Stimme'; });
    $('startup-voice').textContent = !voice || voice.state === 'pending' ? 'WARTET' :
      voice.state === 'ok' ? 'VERBUNDEN' : voice.state === 'loading' ? 'VERBINDET …' : 'NICHT VERBUNDEN';
    $('startup-detail').textContent = this.shutdown ? 'Jarvis beendet laufende Aktionen …' : done + ' / ' + (state.steps || []).length + ' PRÜFUNGEN ABGESCHLOSSEN';
    $('startup-close').textContent = ready ? 'ZUM TAGESBRIEFING →' : 'Im Hintergrund';
    $('startup-cancel').classList.toggle('hidden', ready);
    $('startup-replay').hidden = !(window.JARVIS_PREVIEW && ready);
    this.chip.textContent = ready ? 'START ABGESCHLOSSEN' : this.shutdown ? 'JARVIS STARTET NEU' : 'SYSTEMSTART · ' + pct + '%';
    this.chip.classList.remove('hidden');
    if (ready) {
      this.mainOrb.setState('idle');
      // The demo stays reviewable and can be replayed. Live operation returns to the briefing.
      if (!this.timer && !window.JARVIS_PREVIEW) this.timer = setTimeout(function () {
        this.hide(); this.chip.classList.add('hidden'); this.timer = null;
      }.bind(this), Math.max(state.warnings ? 6500 : 3200, 6800 - (performance.now() - this.startedAt)));
    }
  };
  window.JarvisStartup = Startup;
})();

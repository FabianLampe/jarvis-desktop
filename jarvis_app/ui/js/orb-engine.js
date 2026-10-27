/* J.A.R.V.I.S. Orb-Engine — abgesegnete „Nervenbahnen"-Engine (Seed 5, Spec 2026-06-11),
 * erweitert Richtung Age-of-Ultron-Filmstill:
 *   + Wireframe-Fragmente („Hologramm-Stadt": Paneele/Gitter auf der Kugelschale)
 *   + Schweif-Bögen (helle Teilringe mit nachziehendem Schweif)
 *   + Zustands-Modifikatoren idle/listen/think/speak (weiche 400-ms-Blende)
 *   + optionales amplitude-Feld: Kern & Impulse pulsen zur echten TTS-Stimme
 *
 * Keine Abhängigkeiten; window.JarvisOrb = class.
 */
(function () {
  'use strict';

  var TAU = 6.283185307179586;

  function mulberry32(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function vlen(p) { return Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z); }
  function norm(p) { var l = vlen(p) || 1; return { x: p.x / l, y: p.y / l, z: p.z / l }; }
  function cross(a, b) {
    return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x };
  }

  // Zustands-Modifikatoren laut Spec (Basis = konfigurierte Werte)
  var STATE_MODS = {
    idle:   { bloom: 0.75, speed: 1.0, flows: 1, impulse: 1,   core: 1.0, haloPulse: 0, corePulse: 0 },
    listen: { bloom: 1.3,  speed: 1.0, flows: 1, impulse: 1,   core: 1.3, haloPulse: 1, corePulse: 0 },
    think:  { bloom: 1.1,  speed: 2.5, flows: 2, impulse: 1.3, core: 1.0, haloPulse: 0, corePulse: 0 },
    speak:  { bloom: 1.2,  speed: 1.0, flows: 1, impulse: 2,   core: 1.1, haloPulse: 0, corePulse: 1 }
  };
  var BLEND_TAU = 0.13; // s — erreicht ~95 % in 400 ms (weiche Blende lt. Spec)

  function JarvisOrb(canvas, params) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.P = Object.assign({
      seed: 5, sparks: 250, veins: 25, rays: 4,
      ringb: 0.5, bloom: 0.9, speed: 0.7,
      panels: 90, arcs: 3,            // Film-Look-Erweiterungen
      backdrop: 0.85,                 // dunkler Hof hinter dem Orb (0 = aus) —
                                      // hält ihn auf hellen Wallpapern lesbar
      transparent: true               // App: kein Hintergrund, Desktop scheint durch
    }, params || {});
    if (this.P.panels === undefined || this.P.panels === null) this.P.panels = 90;
    if (this.P.arcs === undefined || this.P.arcs === null) this.P.arcs = 3;
    if (this.P.backdrop === undefined || this.P.backdrop === null) this.P.backdrop = 0.85;

    this.state = 'idle';
    this.mods = Object.assign({}, STATE_MODS.idle);
    this.target = Object.assign({}, STATE_MODS.idle);
    this.offline = false;
    this._dim = 1;                    // geblendeter Offline-Dämpfer
    this.amp = null;                  // letzte TTS-Amplitude (0–1)
    this._ampAt = 0;
    this._ampCur = 0;

    this.tAcc = 2.0;                  // Engine-Zeit (skaliert mit Tempo-Mod)
    this._last = null;
    this._raf = null;

    this.resize(canvas.clientWidth || 500);
    this.build();

    var self = this;
    this._raf = requestAnimationFrame(function step(now) {
      self._frame(now);
      self._raf = requestAnimationFrame(step);
    });
  }

  JarvisOrb.prototype.destroy = function () {
    if (this._raf) cancelAnimationFrame(this._raf);
  };

  JarvisOrb.prototype.resize = function (sizePx) {
    var DPR = window.devicePixelRatio || 1;
    this.S = sizePx;
    this.cv.width = sizePx * DPR; this.cv.height = sizePx * DPR;
    this.cv.style.width = sizePx + 'px'; this.cv.style.height = sizePx + 'px';
    this.ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    this.C = sizePx / 2;
    this.R = sizePx * 0.355;
  };

  JarvisOrb.prototype.setState = function (state, amplitude) {
    if (STATE_MODS[state]) {
      this.state = state;
      this.target = STATE_MODS[state];
    }
    if (amplitude !== null && amplitude !== undefined) {
      this.amp = Math.max(0, Math.min(1, amplitude));
      this._ampAt = performance.now();
    }
  };

  JarvisOrb.prototype.setOffline = function (off) { this.offline = !!off; };

  JarvisOrb.prototype.setParams = function (params) {
    Object.assign(this.P, params || {});
    this.build();
  };

  // ------------------------------------------------------------ Aufbau

  JarvisOrb.prototype._grow = function (start, dir, depth, steps, w0) {
    var rnd = this.rnd;
    var pts = [{ x: start.x, y: start.y, z: start.z }];
    var p = { x: start.x, y: start.y, z: start.z }, d = norm(dir);
    function rv(a) { return { x: (rnd() * 2 - 1) * a, y: (rnd() * 2 - 1) * a, z: (rnd() * 2 - 1) * a }; }
    for (var s = 0; s < steps; s++) {
      var rl = vlen(p) || 0.001;
      var out = { x: p.x / rl, y: p.y / rl, z: p.z / rl };
      var bias = rl < 0.85 ? 0.22 : 0.02;
      var j = rv(0.62);
      d = norm({ x: d.x * 0.72 + out.x * bias + j.x, y: d.y * 0.72 + out.y * bias + j.y, z: d.z * 0.72 + out.z * bias + j.z });
      p = { x: p.x + d.x * 0.046, y: p.y + d.y * 0.046, z: p.z + d.z * 0.046 };
      rl = vlen(p);
      if (rl > 0.985) p = { x: p.x * 0.985 / rl, y: p.y * 0.985 / rl, z: p.z * 0.985 / rl };
      pts.push({ x: p.x, y: p.y, z: p.z });
      if (depth < 2 && s > 3 && rnd() < 0.20) {
        this._grow(p, norm({ x: d.x + (rnd() * 2 - 1) * 1.2, y: d.y + (rnd() * 2 - 1) * 1.2, z: d.z + (rnd() * 2 - 1) * 1.2 }),
          depth + 1, 8 + Math.floor(rnd() * 9), w0 * 0.55);
      }
    }
    this.veins.push({ pts: pts, w0: w0, depth: depth });
  };

  JarvisOrb.prototype.build = function () {
    var P = this.P;
    var rnd = this.rnd = mulberry32(P.seed);
    function rv(a) { return { x: (rnd() * 2 - 1) * a, y: (rnd() * 2 - 1) * a, z: (rnd() * 2 - 1) * a }; }
    function sphPt(r) {
      var a = rnd() * TAU, c = rnd() * 2 - 1, s = Math.sqrt(1 - c * c);
      return { x: r * s * Math.cos(a), y: r * c, z: r * s * Math.sin(a) };
    }

    this.rings = [
      { y0: 0.50,  tx: 0.10,  tz: -0.06, drift: 0.10,  bright: 1.3 },
      { y0: 0.455, tx: 0.10,  tz: -0.06, drift: 0.10,  bright: 0.55 },
      { y0: -0.08, tx: -0.14, tz: 0.10,  drift: -0.06, bright: 0.8 },
      { y0: -0.13, tx: -0.14, tz: 0.10,  drift: -0.06, bright: 0.38 },
      { y0: 0.16,  tx: 0.05,  tz: 0.16,  drift: 0.04,  bright: 0.3 }
    ];

    this.veins = []; this.sparks = []; this.rays = []; this.flows = [];
    this.panels = []; this.arcs = [];

    // Nervenbahnen: dichte Sektoren + Lücken (abgesegnete Referenz)
    var sectors = [norm(rv(1)), norm(rv(1)), norm(rv(1))];
    for (var v = 0; v < P.veins; v++) {
      var sec = sectors[Math.floor(rnd() * sectors.length)];
      var s0 = norm({ x: sec.x + (rnd() * 2 - 1) * 0.8, y: sec.y + (rnd() * 2 - 1) * 0.8, z: sec.z + (rnd() * 2 - 1) * 0.8 });
      s0 = { x: s0.x * 0.12, y: s0.y * 0.12, z: s0.z * 0.12 };
      this._grow(s0, norm(s0), 0, 26 + Math.floor(rnd() * 15), 1.7);
    }
    for (var v2 = 0; v2 < 7; v2++) {
      var s1 = sphPt(0.09 + rnd() * 0.08);
      this._grow(s1, norm(rv(1)), 2, 8 + Math.floor(rnd() * 7), 1.1);
    }

    // Gerade Strahlen + Sehnen
    for (var r1 = 0; r1 < P.rays; r1++) {
      var dr = norm(rv(1));
      this.rays.push({
        a: { x: -dr.x * (0.7 + rnd() * 0.55), y: -dr.y * (0.7 + rnd() * 0.55), z: -dr.z * (0.7 + rnd() * 0.55) },
        b: { x: dr.x * (0.7 + rnd() * 0.6), y: dr.y * (0.7 + rnd() * 0.6), z: dr.z * (0.7 + rnd() * 0.6) },
        tw: rnd() * TAU
      });
    }
    var nch = Math.floor(P.rays * 0.66);
    for (var c1 = 0; c1 < nch; c1++) {
      var pa = sphPt(0.99), pb = sphPt(0.99), tries = 0;
      while (vlen({ x: pa.x - pb.x, y: pa.y - pb.y, z: pa.z - pb.z }) < 1.1 && tries++ < 20) pb = sphPt(0.99);
      this.rays.push({ a: pa, b: pb, tw: rnd() * TAU, chord: true });
    }

    // Goldstaub mit Cluster-Verteilung + Hot-Spots + Bokeh
    var clusters = [];
    for (var cl = 0; cl < 4; cl++) clusters.push(sphPt(1));
    for (var sp = 0; sp < P.sparks; sp++) {
      var base;
      if (rnd() < 0.72) {
        var cc = clusters[Math.floor(rnd() * clusters.length)];
        base = norm({ x: cc.x + (rnd() * 2 - 1) * 0.42, y: cc.y + (rnd() * 2 - 1) * 0.42, z: cc.z + (rnd() * 2 - 1) * 0.42 });
      } else base = norm(rv(1));
      var rr = 0.965 + rnd() * 0.035;
      this.sparks.push({
        x: base.x * rr, y: base.y * rr, z: base.z * rr,
        tw: rnd() * TAU, sp: 0.6 + rnd() * 2.4,
        bokeh: rnd() < 0.13, sz: 0.5 + rnd() * 0.9, hot: rnd() < 0.09
      });
    }
    var nvol = Math.floor(P.sparks * 0.37);
    for (var sv = 0; sv < nvol; sv++) {
      var pv = sphPt(Math.pow(rnd(), 0.7) * 0.88);
      this.sparks.push({
        x: pv.x, y: pv.y, z: pv.z, tw: rnd() * TAU, sp: 0.8 + rnd() * 2.2,
        bokeh: rnd() < 0.10, sz: 0.4 + rnd() * 0.8, hot: false
      });
    }

    // Datenpunkte zwischen den Ringschichten — doppelt bauen, „Denkt" zeigt mehr
    for (var fl = 0; fl < 20; fl++) {
      this.flows.push({ phi: rnd() * TAU, ph: rnd() * TAU, sp: 0.35 + rnd() * 0.5 });
    }

    // Film-Look: Wireframe-Fragmente („Hologramm-Stadt") auf der Kugelschale
    for (var pn = 0; pn < P.panels; pn++) {
      var nrm;
      if (rnd() < 0.7) {
        var cc2 = clusters[Math.floor(rnd() * clusters.length)];
        nrm = norm({ x: cc2.x + (rnd() * 2 - 1) * 0.55, y: cc2.y + (rnd() * 2 - 1) * 0.55, z: cc2.z + (rnd() * 2 - 1) * 0.55 });
      } else nrm = norm(rv(1));
      var u = norm(cross(nrm, vlen(cross(nrm, { x: 0, y: 1, z: 0 })) < 0.1 ? { x: 1, y: 0, z: 0 } : { x: 0, y: 1, z: 0 }));
      var w2 = cross(nrm, u);
      var pw = 0.05 + rnd() * 0.17, ph2 = 0.04 + rnd() * 0.13;
      this.panels.push({
        n: nrm, u: u, v: w2, w: pw, h: ph2,
        r0: 0.84 + rnd() * 0.15,
        grid: rnd() < 0.55 ? 1 + Math.floor(rnd() * 3) : 0,
        cross: rnd() < 0.35 ? 1 + Math.floor(rnd() * 2) : 0,
        dots: rnd() < 0.4,
        tower: rnd() < 0.22 ? 0.025 + rnd() * 0.06 : 0,
        bright: 0.5 + rnd() * 0.5,
        tw: rnd() * TAU
      });
    }

    // Film-Look: Schweif-Bögen (rotierende helle Teilringe mit Nachzieher)
    for (var ar = 0; ar < P.arcs; ar++) {
      this.arcs.push({
        y0: -0.25 + rnd() * 0.65,
        tx: (rnd() * 2 - 1) * 0.5, tz: (rnd() * 2 - 1) * 0.5,
        span: 0.8 + rnd() * 1.0,
        drift: (0.12 + rnd() * 0.22) * (rnd() < 0.5 ? 1 : -1),
        ph: rnd() * TAU,
        bright: 0.65 + rnd() * 0.35
      });
    }
  };

  // ------------------------------------------------------------ Render

  JarvisOrb.prototype._rot = function (p, yaw, pitch) {
    var cy = Math.cos(yaw), sy = Math.sin(yaw);
    var cx = Math.cos(pitch), sx = Math.sin(pitch);
    var x1 = p.x * cy + p.z * sy;
    var z1 = -p.x * sy + p.z * cy;
    return { x: x1, y: p.y * cx - z1 * sx, z: p.y * sx + z1 * cx };
  };
  JarvisOrb.prototype._proj = function (p) {
    var f = 3.4, s = f / (f - p.z);
    return { sx: this.C + p.x * this.R * s, sy: this.C + p.y * this.R * s, z: p.z };
  };

  JarvisOrb.prototype._frame = function (now) {
    var ctx = this.ctx, S = this.S, C = this.C, R = this.R, P = this.P;
    if (this._last === null) this._last = now;
    var dt = Math.min(0.1, (now - this._last) / 1000);
    this._last = now;

    // Zustands-Blende (~400 ms) + Offline-Dämpfer
    var k = 1 - Math.exp(-dt / BLEND_TAU);
    var M = this.mods, T = this.target;
    for (var key in T) M[key] += (T[key] - M[key]) * k;
    this._dim += ((this.offline ? 0.5 : 1) - this._dim) * k;

    this.tAcc += dt * P.speed * M.speed;
    var t = this.tAcc;
    var tR = now / 1000; // Echtzeit für Puls-Perioden (unabhängig vom Tempo-Mod)

    // Amplituden-gesteuerter Sprech-Puls (Filmreferenz: „dialogue waveforms")
    var ampFresh = this.amp !== null && (now - this._ampAt) < 350;
    this._ampCur += ((ampFresh ? this.amp : 0) - this._ampCur) * Math.min(1, dt / 0.06);
    var corePulse = 1, impulseGlow = 1;
    if (M.corePulse > 0.01) {
      var synth = 0.5 + 0.5 * Math.sin(TAU * tR / 0.85);
      var drive = ampFresh ? this._ampCur : synth * 0.55;
      corePulse = 1 + M.corePulse * drive * 0.38;
      impulseGlow = 1 + M.corePulse * drive * 0.8;
    }
    var halo = 1 + M.haloPulse * 0.32 * Math.sin(TAU * tR / 1.1);

    var bloom = P.bloom * M.bloom * this._dim;
    var LW = S / 500; // Linienstärken relativ zur 500-px-Referenz skalieren
    function A(x) { return Math.min(1, x * bloom).toFixed(3); }

    ctx.clearRect(0, 0, S, S);
    if (!P.transparent) {
      ctx.globalCompositeOperation = 'source-over';
      var bg = ctx.createLinearGradient(0, 0, S, S);
      bg.addColorStop(0, '#07080c'); bg.addColorStop(0.5, '#050507'); bg.addColorStop(1, '#06070a');
      ctx.fillStyle = bg; ctx.fillRect(0, 0, S, S);
    } else if (P.backdrop > 0) {
      // Weicher dunkler Hof, damit das additive Glühen auch auf hellen
      // Hintergründen Kontrast hat (Filmumgebung ist dunkel)
      ctx.globalCompositeOperation = 'source-over';
      var bd = ctx.createRadialGradient(C, C, 0, C, C, S * 0.5);
      bd.addColorStop(0, 'rgba(4,6,11,' + Math.min(1, 0.92 * P.backdrop).toFixed(3) + ')');
      bd.addColorStop(0.45, 'rgba(4,6,11,' + Math.min(1, 0.86 * P.backdrop).toFixed(3) + ')');
      bd.addColorStop(0.75, 'rgba(4,6,11,' + Math.min(1, 0.45 * P.backdrop).toFixed(3) + ')');
      bd.addColorStop(1, 'rgba(4,6,11,0)');
      ctx.fillStyle = bd;
      ctx.beginPath(); ctx.arc(C, C, S * 0.5, 0, TAU); ctx.fill();
    }

    var yaw = t * 0.10, pitch = 0.12 + Math.sin(t * 0.19) * 0.05;
    ctx.globalCompositeOperation = 'lighter';
    var i, j, d, p1, p2, tw;

    // --- Nervenbahnen
    for (i = 0; i < this.veins.length; i++) {
      var vn = this.veins[i], pp = [];
      for (j = 0; j < vn.pts.length; j++) pp.push(this._proj(this._rot(vn.pts[j], yaw, pitch)));
      var CH = 4;
      for (var c0 = 0; c0 < pp.length - 1; c0 += CH) {
        var cEnd = Math.min(c0 + CH, pp.length - 1);
        var zm = 0; for (j = c0; j <= cEnd; j++) zm += pp[j].z;
        d = (zm / (cEnd - c0 + 1) + 1) / 2;
        var prog = c0 / pp.length;
        var w = vn.w0 * (1 - prog * 0.75) * LW;
        ctx.beginPath(); ctx.moveTo(pp[c0].sx, pp[c0].sy);
        for (j = c0 + 1; j <= cEnd; j++) ctx.lineTo(pp[j].sx, pp[j].sy);
        ctx.strokeStyle = 'rgba(255,150,35,' + A((0.05 + d * 0.16) * (1 - prog * 0.4)) + ')';
        ctx.lineWidth = w * 2.6; ctx.stroke();
        var gcol = Math.floor(235 - prog * 50), bcol = Math.floor(185 - prog * 110);
        ctx.beginPath(); ctx.moveTo(pp[c0].sx, pp[c0].sy);
        for (j = c0 + 1; j <= cEnd; j++) ctx.lineTo(pp[j].sx, pp[j].sy);
        ctx.strokeStyle = 'rgba(255,' + gcol + ',' + bcol + ',' + Math.min(1, (0.10 + d * 0.42) * this._dim).toFixed(3) + ')';
        ctx.lineWidth = Math.max(0.4, w * 0.85); ctx.stroke();
      }
    }

    // --- Gerade Strahlen / Sehnen
    for (i = 0; i < this.rays.length; i++) {
      var ry = this.rays[i];
      p1 = this._proj(this._rot(ry.a, yaw, pitch)); p2 = this._proj(this._rot(ry.b, yaw, pitch));
      d = ((p1.z + p2.z) / 2 + 1) / 2;
      tw = 0.75 + 0.25 * Math.sin(ry.tw + t * 1.3);
      ctx.strokeStyle = 'rgba(255,230,170,' + (((ry.chord ? 0.08 : 0.12) + d * (ry.chord ? 0.20 : 0.30)) * tw * this._dim).toFixed(3) + ')';
      ctx.lineWidth = Math.max(0.45, 0.7 * LW);
      ctx.beginPath(); ctx.moveTo(p1.sx, p1.sy); ctx.lineTo(p2.sx, p2.sy); ctx.stroke();
    }

    // --- Wireframe-Fragmente (Hologramm-Stadt)
    for (i = 0; i < this.panels.length; i++) {
      var pl = this.panels[i];
      var corners = [];
      for (j = 0; j < 4; j++) {
        var su = (j === 0 || j === 3) ? -1 : 1;
        var sv2 = (j < 2) ? -1 : 1;
        var pt = {
          x: (pl.n.x * pl.r0 + pl.u.x * pl.w * su + pl.v.x * pl.h * sv2),
          y: (pl.n.y * pl.r0 + pl.u.y * pl.w * su + pl.v.y * pl.h * sv2),
          z: (pl.n.z * pl.r0 + pl.u.z * pl.w * su + pl.v.z * pl.h * sv2)
        };
        corners.push(this._proj(this._rot(pt, yaw, pitch)));
      }
      var zm3 = (corners[0].z + corners[1].z + corners[2].z + corners[3].z) / 4;
      d = (zm3 + 1) / 2;
      tw = 0.8 + 0.2 * Math.sin(pl.tw + t * 0.9);
      var aP = (0.10 + d * 0.55) * pl.bright * tw;
      ctx.strokeStyle = 'rgba(255,190,85,' + A(aP) + ')';
      ctx.lineWidth = Math.max(0.4, 0.7 * LW);
      ctx.beginPath();
      ctx.moveTo(corners[0].sx, corners[0].sy);
      ctx.lineTo(corners[1].sx, corners[1].sy);
      ctx.lineTo(corners[2].sx, corners[2].sy);
      ctx.lineTo(corners[3].sx, corners[3].sy);
      ctx.closePath(); ctx.stroke();
      // Gitterlinien im Paneel (längs + quer = „Code-Blöcke" wie im Film)
      for (var g = 1; g <= pl.grid; g++) {
        var f2 = g / (pl.grid + 1);
        var ga = {
          x: corners[0].sx + (corners[1].sx - corners[0].sx) * f2,
          y: corners[0].sy + (corners[1].sy - corners[0].sy) * f2
        };
        var gb = {
          x: corners[3].sx + (corners[2].sx - corners[3].sx) * f2,
          y: corners[3].sy + (corners[2].sy - corners[3].sy) * f2
        };
        ctx.strokeStyle = 'rgba(255,190,85,' + A(aP * 0.65) + ')';
        ctx.beginPath(); ctx.moveTo(ga.x, ga.y); ctx.lineTo(gb.x, gb.y); ctx.stroke();
      }
      for (var g2 = 1; g2 <= pl.cross; g2++) {
        var f3 = g2 / (pl.cross + 1);
        var gc = {
          x: corners[0].sx + (corners[3].sx - corners[0].sx) * f3,
          y: corners[0].sy + (corners[3].sy - corners[0].sy) * f3
        };
        var gd = {
          x: corners[1].sx + (corners[2].sx - corners[1].sx) * f3,
          y: corners[1].sy + (corners[2].sy - corners[1].sy) * f3
        };
        ctx.strokeStyle = 'rgba(255,190,85,' + A(aP * 0.55) + ')';
        ctx.beginPath(); ctx.moveTo(gc.x, gc.y); ctx.lineTo(gd.x, gd.y); ctx.stroke();
      }
      // helle Eckpunkte („Fenster"/Knoten)
      if (pl.dots) {
        ctx.fillStyle = 'rgba(255,235,180,' + A(aP * 1.5) + ')';
        for (j = 0; j < 4; j += 2) {
          ctx.beginPath();
          ctx.arc(corners[j].sx, corners[j].sy, Math.max(0.5, 0.9 * LW), 0, TAU);
          ctx.fill();
        }
      }
      // „Türme": kurze Kanten nach außen
      if (pl.tower > 0) {
        for (j = 0; j < 4; j += 3) {
          var su2 = (j === 0 || j === 3) ? -1 : 1;
          var sv3 = (j < 2) ? -1 : 1;
          var basePt = {
            x: pl.n.x * pl.r0 + pl.u.x * pl.w * su2 + pl.v.x * pl.h * sv3,
            y: pl.n.y * pl.r0 + pl.u.y * pl.w * su2 + pl.v.y * pl.h * sv3,
            z: pl.n.z * pl.r0 + pl.u.z * pl.w * su2 + pl.v.z * pl.h * sv3
          };
          var tip = {
            x: basePt.x + pl.n.x * pl.tower, y: basePt.y + pl.n.y * pl.tower, z: basePt.z + pl.n.z * pl.tower
          };
          p1 = this._proj(this._rot(basePt, yaw, pitch));
          p2 = this._proj(this._rot(tip, yaw, pitch));
          ctx.strokeStyle = 'rgba(255,210,120,' + A(aP * 0.9) + ')';
          ctx.beginPath(); ctx.moveTo(p1.sx, p1.sy); ctx.lineTo(p2.sx, p2.sy); ctx.stroke();
        }
      }
    }

    // --- Ringbänder (Gyroskop-Breitenkreise)
    for (i = 0; i < this.rings.length; i++) {
      var rg = this.rings[i];
      var rr = Math.sqrt(Math.max(0, 1 - rg.y0 * rg.y0));
      var segs = [];
      for (var ang = 0; ang <= 6.30; ang += 0.045) {
        var aa = ang + t * rg.drift;
        var pr = { x: Math.cos(aa) * rr, y: rg.y0, z: Math.sin(aa) * rr };
        var py = pr.y * Math.cos(rg.tx) - pr.z * Math.sin(rg.tx);
        var pz = pr.y * Math.sin(rg.tx) + pr.z * Math.cos(rg.tx);
        pr = { x: pr.x * Math.cos(rg.tz) - py * Math.sin(rg.tz), y: pr.x * Math.sin(rg.tz) + py * Math.cos(rg.tz), z: pz };
        segs.push(this._proj(this._rot(pr, yaw, pitch)));
      }
      var CH2 = 9;
      for (var q0 = 0; q0 < segs.length - 1; q0 += CH2) {
        var q1 = Math.min(q0 + CH2, segs.length - 1);
        var zm2 = 0; for (j = q0; j <= q1; j++) zm2 += segs[j].z;
        d = (zm2 / (q1 - q0 + 1) + 1) / 2;
        var AA = rg.bright * P.ringb * (0.10 + d * 0.55);
        ctx.beginPath(); ctx.moveTo(segs[q0].sx, segs[q0].sy);
        for (j = q0 + 1; j <= q1; j++) ctx.lineTo(segs[j].sx, segs[j].sy);
        ctx.strokeStyle = 'rgba(255,150,35,' + A(AA * 0.22) + ')'; ctx.lineWidth = 6.5 * LW; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(segs[q0].sx, segs[q0].sy);
        for (j = q0 + 1; j <= q1; j++) ctx.lineTo(segs[j].sx, segs[j].sy);
        ctx.strokeStyle = 'rgba(255,185,75,' + A(AA * 0.5) + ')'; ctx.lineWidth = 2.6 * LW; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(segs[q0].sx, segs[q0].sy);
        for (j = q0 + 1; j <= q1; j++) ctx.lineTo(segs[j].sx, segs[j].sy);
        ctx.strokeStyle = 'rgba(255,235,185,' + A(AA * 0.85) + ')'; ctx.lineWidth = Math.max(0.5, 1.0 * LW); ctx.stroke();
      }
    }

    // --- Schweif-Bögen (Film-Look)
    for (i = 0; i < this.arcs.length; i++) {
      var ac = this.arcs[i];
      var rrA = Math.sqrt(Math.max(0, 1 - ac.y0 * ac.y0)) * 1.005;
      var head = ac.ph + t * ac.drift;
      var NSEG = 16;
      for (var sgi = 0; sgi < NSEG; sgi++) {
        var fade = 1 - sgi / NSEG;                  // 1 am Kopf → 0 am Schweifende
        var a0 = head - ac.span * (sgi + 1) / NSEG;
        var a1 = head - ac.span * sgi / NSEG;
        var sgPts = [];
        for (var ss = 0; ss <= 2; ss++) {
          var aa2 = a0 + (a1 - a0) * ss / 2;
          var pr2 = { x: Math.cos(aa2) * rrA, y: ac.y0, z: Math.sin(aa2) * rrA };
          var py2 = pr2.y * Math.cos(ac.tx) - pr2.z * Math.sin(ac.tx);
          var pz2 = pr2.y * Math.sin(ac.tx) + pr2.z * Math.cos(ac.tx);
          pr2 = { x: pr2.x * Math.cos(ac.tz) - py2 * Math.sin(ac.tz), y: pr2.x * Math.sin(ac.tz) + py2 * Math.cos(ac.tz), z: pz2 };
          sgPts.push(this._proj(this._rot(pr2, yaw, pitch)));
        }
        d = ((sgPts[0].z + sgPts[2].z) / 2 + 1) / 2;
        var aArc = ac.bright * (0.10 + d * 0.65) * fade * fade;
        ctx.strokeStyle = 'rgba(255,205,115,' + A(aArc) + ')';
        ctx.lineWidth = 2.1 * LW * (0.4 + fade);
        ctx.beginPath(); ctx.moveTo(sgPts[0].sx, sgPts[0].sy);
        ctx.lineTo(sgPts[1].sx, sgPts[1].sy); ctx.lineTo(sgPts[2].sx, sgPts[2].sy);
        ctx.stroke();
        if (sgi === 0) { // heller Kopfpunkt mit kleinem Hof
          ctx.fillStyle = 'rgba(255,248,228,' + A(ac.bright * (0.35 + d * 0.55)) + ')';
          ctx.beginPath(); ctx.arc(sgPts[2].sx, sgPts[2].sy, (1.6 + d * 1.3) * LW, 0, TAU); ctx.fill();
          ctx.fillStyle = 'rgba(255,200,100,' + A(ac.bright * (0.10 + d * 0.16)) + ')';
          ctx.beginPath(); ctx.arc(sgPts[2].sx, sgPts[2].sy, (4.5 + d * 2.5) * LW, 0, TAU); ctx.fill();
        }
      }
    }

    // --- Datenpunkte zwischen den Ringschichten
    var nFlows = Math.max(1, Math.min(this.flows.length, Math.round(10 * M.flows)));
    for (i = 0; i < nFlows; i++) {
      var fwk = this.flows[i];
      for (var trail = 0; trail < 4; trail++) {
        var tt = t - trail * 0.06;
        var mix = 0.5 + 0.5 * Math.sin(fwk.ph + tt * fwk.sp * M.impulse);
        var yy = this.rings[0].y0 + (this.rings[2].y0 - this.rings[0].y0) * mix;
        var rr2 = Math.sqrt(Math.max(0, 1 - yy * yy)) * 0.99;
        var pf = { x: Math.cos(fwk.phi) * rr2, y: yy, z: Math.sin(fwk.phi) * rr2 };
        p1 = this._proj(this._rot(pf, yaw, pitch));
        d = (p1.z + 1) / 2;
        ctx.fillStyle = 'rgba(255,240,200,' + Math.min(1, (0.4 + d * 0.5) * (1 - trail * 0.24) * impulseGlow * this._dim).toFixed(3) + ')';
        ctx.beginPath(); ctx.arc(p1.sx, p1.sy, (1.3 - trail * 0.22 + d * 0.7) * LW, 0, TAU); ctx.fill();
      }
    }

    // --- Goldstaub
    for (i = 0; i < this.sparks.length; i++) {
      var n = this.sparks[i];
      p1 = this._proj(this._rot(n, yaw, pitch));
      d = (p1.z + 1) / 2;
      if (n.bokeh) {
        ctx.fillStyle = 'rgba(255,200,110,' + A(0.04 + d * 0.07) + ')';
        ctx.beginPath(); ctx.arc(p1.sx, p1.sy, (2.6 + n.sz * 3.2) * LW, 0, TAU); ctx.fill();
      } else if (n.hot) {
        tw = Math.max(0, Math.sin(n.tw + t * 2.6 * n.sp));
        ctx.fillStyle = 'rgba(255,248,230,' + A((0.25 + d * 0.7) * tw) + ')';
        ctx.beginPath(); ctx.arc(p1.sx, p1.sy, (n.sz * (1.0 + d * 1.6) * (0.6 + tw * 0.4) + 0.3) * LW, 0, TAU); ctx.fill();
        ctx.fillStyle = 'rgba(255,190,90,' + A((0.10 + d * 0.18) * tw) + ')';
        ctx.beginPath(); ctx.arc(p1.sx, p1.sy, n.sz * 3.2 * (0.6 + tw * 0.4) * LW, 0, TAU); ctx.fill();
      } else {
        tw = Math.max(0, Math.sin(n.tw + t * 2.6 * n.sp));
        ctx.fillStyle = 'rgba(255,' + (205 + Math.floor(d * 40)) + ',' + (115 + Math.floor(d * 75)) + ',' + Math.min(1, (0.16 + d * 0.68) * tw * this._dim).toFixed(3) + ')';
        ctx.beginPath(); ctx.arc(p1.sx, p1.sy, (n.sz * (0.7 + d * 1.2) * (0.5 + tw * 0.5) + 0.25) * LW, 0, TAU); ctx.fill();
      }
    }

    // --- Auge: Kern, Iris-Ring, Wirbelbögen (Kern reagiert auf Zustand/Stimme)
    var coreR = R * 0.26 * M.core * corePulse;
    var cg = ctx.createRadialGradient(C, C, 0, C, C, coreR);
    cg.addColorStop(0, 'rgba(255,250,238,' + Math.min(1, 0.9 * this._dim) + ')');
    cg.addColorStop(0.18, 'rgba(255,230,160,' + Math.min(1, 0.55 * this._dim) + ')');
    cg.addColorStop(0.5, 'rgba(255,170,60,' + Math.min(1, 0.18 * this._dim) + ')');
    cg.addColorStop(1, 'rgba(255,130,30,0)');
    ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(C, C, coreR, 0, TAU); ctx.fill();
    var pul = (1 + 0.06 * Math.sin(t * 2.2)) * corePulse;
    ctx.strokeStyle = 'rgba(255,246,220,' + Math.min(1, 0.9 * this._dim) + ')'; ctx.lineWidth = Math.max(0.8, 2.0 * LW);
    ctx.beginPath(); ctx.arc(C, C, R * 0.085 * M.core * pul, 0, TAU); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,210,120,' + Math.min(1, 0.45 * this._dim) + ')'; ctx.lineWidth = Math.max(0.6, 1.1 * LW);
    ctx.beginPath(); ctx.arc(C, C, R * 0.13, 0.6 + t * 0.9, 2.5 + t * 0.9); ctx.stroke();
    ctx.beginPath(); ctx.arc(C, C, R * 0.175, 3.8 - t * 0.65, 5.2 - t * 0.65); ctx.stroke();

    // --- Glühen (außen + innen), Halo pulsiert beim Zuhören
    var og = ctx.createRadialGradient(C, C, R * 0.5, C, C, R * 1.45);
    og.addColorStop(0, 'rgba(255,150,45,' + A(0.13 * halo) + ')');
    og.addColorStop(1, 'rgba(255,120,20,0)');
    ctx.fillStyle = og; ctx.fillRect(0, 0, S, S);
    var ig = ctx.createRadialGradient(C, C, 0, C, C, R * 0.95);
    ig.addColorStop(0, 'rgba(255,170,60,' + A(0.07) + ')');
    ig.addColorStop(0.6, 'rgba(255,150,45,' + A(0.045 * halo) + ')');
    ig.addColorStop(1, 'rgba(255,130,30,0)');
    ctx.fillStyle = ig; ctx.beginPath(); ctx.arc(C, C, R * 0.95, 0, TAU); ctx.fill();
  };

  window.JarvisOrb = JarvisOrb;
})();

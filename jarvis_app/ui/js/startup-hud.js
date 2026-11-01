/* Interface boot choreography around the existing JarvisOrb.
 * This layer only draws connecting lines and corner markers, never the orb itself.
 */
(function () {
  'use strict';
  function clamp(v) { return Math.max(0, Math.min(1, v)); }
  function ease(v) { v = clamp(v); return 1 - Math.pow(1 - v, 3); }

  function Hud(canvas, reduced) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.reduced = reduced;
    this.started = performance.now();
    this.phase = 'running';
    this.phaseAt = this.started;
    this.lastFrame = 0;
    this.dead = false;
    this.resize = this.resize.bind(this);
    this.frame = this.frame.bind(this);
    this.observer = new ResizeObserver(this.resize);
    this.observer.observe(canvas.parentElement);
    this.resize();
    this.frame(this.started);
  }
  Hud.prototype.resize = function () {
    var rect = this.cv.parentElement.getBoundingClientRect();
    this.w = Math.max(1, rect.width); this.h = Math.max(1, rect.height);
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.cv.width = Math.round(this.w * dpr); this.cv.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (this.reduced) this.draw(performance.now());
  };
  Hud.prototype.setState = function (state) {
    var phase = state.phase === 'shutdown' ? 'shutdown' : state.phase === 'ready' ? 'ready' : 'running';
    if (phase !== this.phase) { this.phase = phase; this.phaseAt = performance.now(); }
    if (this.reduced) this.draw(performance.now());
  };
  Hud.prototype.destroy = function () {
    this.dead = true;
    cancelAnimationFrame(this.raf);
    this.observer.disconnect();
  };
  Hud.prototype.frame = function (now) {
    if (this.dead) return;
    if (now - this.lastFrame >= 1000 / 30 || !this.lastFrame) {
      this.draw(now); this.lastFrame = now;
    }
    if (!this.reduced) this.raf = requestAnimationFrame(this.frame);
  };
  Hud.prototype.draw = function (now) {
    var c = this.ctx, w = this.w, h = this.h;
    if (!w || !h) return;
    c.clearRect(0, 0, w, h);
    var t = this.reduced ? 10 : (now - this.started) / 1000;
    var off = this.phase === 'shutdown' ? (this.reduced ? 0.85 : ease((now - this.phaseAt) / 1600)) : 0;
    var on = ease((t - 0.65) / 1.45) * (1 - off);
    var cx = w / 2, cy = h / 2;
    var r = Math.min(h * 0.43, w * (w < 560 ? 0.43 : 0.29));
    function line(points, alpha, gold, amount) {
      c.strokeStyle = gold ? 'rgba(230,181,108,' + alpha + ')' : 'rgba(119,183,202,' + alpha + ')';
      c.lineWidth = 0.8;
      c.beginPath(); c.moveTo(points[0][0], points[0][1]);
      var segments = points.length - 1, reveal = clamp(amount) * segments;
      for (var i = 1; i <= segments; i++) {
        var p = clamp(reveal - i + 1);
        c.lineTo(points[i - 1][0] + (points[i][0] - points[i - 1][0]) * p,
                 points[i - 1][1] + (points[i][1] - points[i - 1][1]) * p);
        if (p < 1) break;
      }
      c.stroke();
    }
    // Fine leaders grow outwards from the unchanged orb into the interface panels.
    for (var side = -1; side <= 1; side += 2) {
      for (var vertical = -1; vertical <= 1; vertical += 2) {
        var sx = cx + side * r * 0.82, sy = cy + vertical * r * 0.58;
        var mx = cx + side * r * 1.08, my = cy + vertical * r * 0.8;
        line([[sx, sy], [mx, my], [cx + side * (w / 2 - 30), my]], 0.34 * on, false, on);
        // Open corner brackets, deliberately outside the orb silhouette.
        var bx = cx + side * r, by = cy + vertical * r * 0.94;
        line([[bx - side * 14, by], [bx, by], [bx, by - vertical * 14]], 0.55 * on, true, on);
      }
      for (var k = -2; k <= 2; k++) {
        var x = cx + side * Math.min(w / 2 - 14, r * 1.5), y = cy + k * r * 0.36;
        line([[x - 2, y], [x + 2, y]], 0.25 * on, false, 1);
        line([[x, y - 2], [x, y + 2]], 0.25 * on, false, 1);
      }
    }
    // A single initialization sweep resolves into the console rail, then stops.
    var sweep = clamp((t - 1.1) / 1.7);
    if (sweep > 0 && sweep < 1 && !this.reduced) {
      var yy = h * (0.12 + sweep * 0.78);
      var gradient = c.createLinearGradient(cx - r, 0, cx + r, 0);
      gradient.addColorStop(0, 'rgba(230,181,108,0)');
      gradient.addColorStop(0.5, 'rgba(230,181,108,' + Math.sin(sweep * Math.PI) * 0.28 + ')');
      gradient.addColorStop(1, 'rgba(230,181,108,0)');
      c.fillStyle = gradient; c.fillRect(cx - r, yy, r * 2, 1);
    }
  };
  window.JarvisStartupHUD = Hud;
})();

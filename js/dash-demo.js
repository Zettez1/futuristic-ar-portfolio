(function () {
  "use strict";

  var T = window.FSD_T || function (s) { return s; };

  var cv = document.getElementById("dash-canvas");
  if (!cv) return;
  var ctx = cv.getContext("2d");

  var W = 0, H = 0;
  function resize() {
    var r = cv.getBoundingClientRect();
    W = r.width;
    H = r.height;
    cv.width = W * devicePixelRatio;
    cv.height = H * devicePixelRatio;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  }
  resize();
  window.addEventListener("resize", resize);

  var DAYS = 90;
  var ltv = [], conv = [], mrr = [];

  function rnd(a, b) { return a + Math.random() * (b - a); }

  function seed() {
    ltv = []; conv = []; mrr = [];
    var l = 140, c = 2.1, m = 2400;
    for (var i = 0; i < TTL; i++) {
      l += rnd(-1.2, 3.4) + 0.28;
      c += rnd(-0.14, 0.2) + 0.012;
      m += rnd(-30, 70) + 16;
      ltv.push(Math.max(120, l));
      conv.push(Math.max(1.4, c));
      mrr.push(Math.max(1800, m));
    }
    var k1 = ltv[TTL - 1] / TTL * 1.05;
    var k3 = mrr[TTL - 1] / TTL * 2.4;
    for (var j = TTL; j < DAYS; j++) {
      ltv.push(ltv[j - 1] + k1);
      conv.push(conv[j - 1] + 0.006);
      mrr.push(mrr[j - 1] + k3);
    }
  }
  seed();

  function panel(x, y, w, h, title, data, color) {
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    ctx.fillStyle = "rgba(255,255,255,0.05)";
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = "#94a3b8";
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.fillText(title, x + 10, y + 14);
    ctx.fillStyle = color;
    ctx.font = "600 13px 'JetBrains Mono', monospace";
    ctx.fillText("$ " + Math.round(data[data.length - 1]).toLocaleString("en-US"), x + w - 74, y + 14);

    var padL = 26, padB = 16, padT = 20;
    var pw = w - padL - 8, ph = h - padT - padB;
    var max = Math.max.apply(null, data) * 1.15;
    var min = Math.min.apply(null, data) * 0.85;

    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.beginPath();
    for (var g = 1; g < 4; g++) {
      var gy = y + padT + ph * g / 4;
      ctx.moveTo(x + padL, gy);
      ctx.lineTo(x + w - 8, gy);
    }
    ctx.stroke();

    ctx.fillStyle = "rgba(148,163,184,0.7)";
    ctx.font = "8.5px 'JetBrains Mono', monospace";
    ctx.fillText(Math.round(max).toLocaleString("en-US"), x + 2, y + padT + 8);
    ctx.fillText(Math.round(min).toLocaleString("en-US"), x + 2, y + h - padB - 2);

    var visible = DAYS - TTL;
    function put(v0, alpha, dash) {
      ctx.beginPath();
      var started = false;
      for (var i = v0; i < data.length; i++) {
        var px = x + padL + (i / (DAYS - 1)) * pw;
        var py = y + padT + ph - ((data[i] - min) / (max - min)) * ph;
        if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
      }
      ctx.strokeStyle = "rgba(148,163,184," + alpha + ")";
      ctx.setLineDash(dash || []);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    put(0, 1, null);
    put(TTL, 0.28, [4, 4]);

    var lastX = x + padL + pw, lastY = y + padT + ph - ((data[data.length - 1] - min) / (max - min)) * ph;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(lastX, lastY, 3, 0, Math.PI * 2);
    ctx.fill();

    var b = data[data.length - 1];
    var b0 = data[Math.max(0, data.length - 8)];
    var delta = (b - b0) / b0 * 100;
    ctx.fillStyle = delta >= 0 ? "#34d399" : "#f87171";
    ctx.font = "9px 'JetBrains Mono', monospace";
    ctx.fillText((delta >= 0 ? "▲" : "▼") + " " + Math.abs(delta).toFixed(1) + "% / " + T("7д"), x + 10, y + h - 6);
  }

  var TTL = 60;

  var vis = false;
  var io = new IntersectionObserver(function (en) {
    vis = en[0].isIntersecting;
  }, { threshold: 0.15 });
  io.observe(cv);

  var btn = document.getElementById("dash-run");
  if (btn) btn.addEventListener("click", function () { seed(); });

  function frame() {
    requestAnimationFrame(frame);
    if (!vis) return;
    ltv[TTL - 1] += rnd(-0.9, 0.9) + 0.12;
    conv[TTL - 1] = Math.max(1.4, conv[TTL - 1] + rnd(-0.06, 0.06));
    mrr[TTL - 1] = Math.max(1800, mrr[TTL - 1] + rnd(-25, 25) + 6);

    ctx.clearRect(0, 0, W, H);
    panel(0, 0, W, H / 2 - 5, T("LTV (життєва цінність клієнта)"), ltv, "#22d3ee");
    panel(0, H / 2 + 5, W / 2 - 5, H / 2 - 5, T("Конверсія, %"), conv, "#34d399");
    panel(W / 2 + 5, H / 2 + 5, W / 2 - 5, H / 2 - 5, T("MRR (місячний дохід)"), mrr, "#a78bfa");
  }
  frame();
})();
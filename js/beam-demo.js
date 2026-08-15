(function () {
  "use strict";

  var els = {};
  ["beam-l", "beam-q", "beam-l-val", "beam-q-val", "beam-profile", "beam-svg",
   "res-moment", "res-stress", "res-deflect", "res-weight", "res-price",
   "beam-status", "beam-status"]
    .forEach(function (id) { els[id] = document.getElementById(id); });
  if (!els["beam-l"]) return;

  var profile = 20;
  var requestId = 0;

  function setStatus(text, cls) {
    els["beam-status"].textContent = text;
    els["beam-status"].className = "status-pill " + (cls || "status-wait");
  }

  function drawBeam(L, q) {
    var svg = els["beam-svg"];
    var NS = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";

    var pad = 24, w = 320, h = 160, top = 40, base = 118;
    var x0 = pad, x1 = w - pad;
    var grad = document.createElementNS(NS, "linearGradient");
    grad.id = "beamGrad";
    grad.setAttribute("x1", "0"); grad.setAttribute("y1", "0");
    grad.setAttribute("x2", "1"); grad.setAttribute("y2", "0");
    var st1 = document.createElementNS(NS, "stop");
    st1.setAttribute("offset", "0"); st1.setAttribute("stop-color", "#22d3ee");
    var st2 = document.createElementNS(NS, "stop");
    st2.setAttribute("offset", "1"); st2.setAttribute("stop-color", "#a78bfa");
    grad.appendChild(st1); grad.appendChild(st2);
    svg.appendChild(grad);

    var maxQ = 60, maxDefl = 30;
    var deflRatio = (q / maxQ) * 0.7 + 0.15;

    /* supports */
    function tri(cx, up) {
      var p = document.createElementNS(NS, "polygon");
      p.setAttribute("points",
        cx - 10 + "," + base + " " + cx + "," + (base - (up ? 0 : 16)) + " " + (cx + 10) + "," + base);
      p.setAttribute("fill", "rgba(148,163,255,0.25)");
      p.setAttribute("stroke", "rgba(148,163,255,0.6)");
      svg.appendChild(p);
    }
    tri(x0, true); tri(x1, true);

    /* arrows */
    var n = 9;
    for (var i = 1; i < n; i++) {
      var x = x0 + ((x1 - x0) * i) / n;
      var line = document.createElementNS(NS, "line");
      line.setAttribute("x1", x); line.setAttribute("y1", top);
      line.setAttribute("x2", x); line.setAttribute("y2", top + 14 + 26 * deflRatio);
      line.setAttribute("stroke", "rgba(34,211,238,0.55)");
      line.setAttribute("stroke-width", "2");
      svg.appendChild(line);
      var hd = document.createElementNS(NS, "path");
      hd.setAttribute("d", "M" + (x - 5) + " " + (top + 16 + 26 * deflRatio) + " l5 -8 l5 8 z");
      hd.setAttribute("fill", "rgba(34,211,238,0.7)");
      svg.appendChild(hd);
    }

    /* sag beam */
    var midSag = 26 * deflRatio;
    var pts = [];
    for (var j = 0; j <= 20; j++) {
      var px = x0 + ((x1 - x0) * j) / 20;
      var py = base - midSag * 4 * ((j / 20) * (1 - j / 20));
      pts.push(px + "," + py);
    }
    var poly = document.createElementNS(NS, "polyline");
    poly.setAttribute("points", pts.join(" "));
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", "url(#beamGrad)");
    poly.setAttribute("stroke-width", "5");
    poly.setAttribute("stroke-linecap", "round");
    svg.appendChild(poly);

    /* dims */
    var dim = document.createElementNS(NS, "line");
    dim.setAttribute("x1", x0 + 4); dim.setAttribute("y1", h - 6);
    dim.setAttribute("x2", x1 - 4); dim.setAttribute("y2", h - 6);
    dim.setAttribute("stroke", "#3b4a66"); dim.setAttribute("stroke-width", "1");
    svg.appendChild(dim);
    var dimT = document.createElementNS(NS, "text");
    dimT.setAttribute("x", w / 2); dimT.setAttribute("y", h - 14);
    dimT.setAttribute("text-anchor", "middle");
    dimT.setAttribute("fill", "#64748b"); dimT.setAttribute("font-size", "11");
    dimT.setAttribute("font-family", "JetBrains Mono, monospace");
    dimT.textContent = "L = " + L + " м · q = " + q + " кН/м";
    svg.appendChild(dimT);
  }

  function calc() {
    var L = parseFloat(els["beam-l"].value) || 6;
    var q = parseFloat(els["beam-q"].value) || 25;
    els["beam-l-val"].textContent = L + " м";
    els["beam-q-val"].textContent = q + " кН/м";
    drawBeam(L, q);

    var id = ++requestId;
    setStatus("Розрахунок на сервері…", "status-wait");

    fetch("/api/calc/beam?" + new URLSearchParams({ profile: String(profile), length: String(L), load: String(q) }))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (id !== requestId || !d.ok) return;
        els["res-moment"].textContent = d.moment.toFixed(1) + " кН·м";
        els["res-stress"].textContent = d.stress.toFixed(1) + " МПа";
        els["res-deflect"].textContent = (d.deflection * 1000).toFixed(1) + " мм";
        els["res-weight"].textContent = d.weight.toFixed(1) + " кг";
        els["res-price"].textContent = d.price.toLocaleString("uk-UA") + " грн";
        if (d.passed) {
          setStatus("ПРОФІЛЬ ПРОХОДИТЬ · запас " + d.margin + "%", "status-ok");
        } else {
          setStatus("ПРОФІЛЬ НЕ ПРОХОДИТЬ · потрібен більший переріз", "status-fail");
        }
      })
      .catch(function () {
        if (id !== requestId) return;
        setStatus("Офлайн-режим: сервіс недоступний", "status-fail");
        els["res-moment"].textContent = "—";
        els["res-stress"].textContent = "—";
        els["res-deflect"].textContent = "—";
        els["res-weight"].textContent = "—";
        els["res-price"].textContent = "—";
      });
  }

  els["beam-profile"].addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    els["beam-profile"].querySelectorAll("button").forEach(function (b) { b.classList.remove("active"); });
    btn.classList.add("active");
    profile = parseInt(btn.getAttribute("data-profile"), 10);
    calc();
  });

  els["beam-l"].addEventListener("input", calc);
  els["beam-q"].addEventListener("input", calc);

  var revealO = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) { calc(); revealO.unobserve(en.target); }
    });
  }, { threshold: 0.3 });
  revealO.observe(els["beam-l"]);
})();
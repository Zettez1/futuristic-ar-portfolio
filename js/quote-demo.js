(function () {
  "use strict";

  var ids = ["quote-team", "quote-complexity", "quote-team-val", "quote-complexity-val",
             "quote-type", "quote-svg", "res-cost", "res-weeks", "res-hours",
             "res-support", "res-price", "quote-status"];
  var els = {};
  ids.forEach(function (id) { els[id] = document.getElementById(id); });
  if (!els["quote-svg"]) return;

  var ptype = "landing";
  var requestId = 0;
  var COMPLEXITY_NAMES = { 1: "базова", 2: "середня", 3: "складна" };

  function setStatus(text, cls) {
    els["quote-status"].textContent = text;
    els["quote-status"].className = "status-pill " + (cls || "status-wait");
  }

  function drawTimeline(weeks, team) {
    var svg = els["quote-svg"];
    var NS = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";

    var w = 320, h = 160, pad = 24;
    var sprints = Math.max(weeks, 1);
    var barH = Math.min(34 + team * 3, 68);
    var gutter = 14;
    var y = 52;
    var totalW = w - pad * 2 - gutter * (sprints - 1);
    var bw = totalW / sprints;

    var grad = document.createElementNS(NS, "linearGradient");
    grad.id = "taskGrad";
    grad.setAttribute("x1", "0"); grad.setAttribute("y1", "0");
    grad.setAttribute("x2", "0"); grad.setAttribute("y2", "1");
    var st1 = document.createElementNS(NS, "stop");
    st1.setAttribute("offset", "0"); st1.setAttribute("stop-color", "#22d3ee");
    var st2 = document.createElementNS(NS, "stop");
    st2.setAttribute("offset", "1"); st2.setAttribute("stop-color", "#a78bfa");
    grad.appendChild(st1); grad.appendChild(st2);
    svg.appendChild(grad);

    var label = document.createElementNS(NS, "text");
    label.setAttribute("x", w / 2); label.setAttribute("y", 30);
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("fill", "#94a3b8"); label.setAttribute("font-size", "12");
    label.setAttribute("font-family", "JetBrains Mono, monospace");
    label.textContent = "sprint-план: " + sprints + " тиж";
    svg.appendChild(label);

    for (var i = 0; i < sprints; i++) {
      var x = pad + i * (bw + gutter);
      var bh = barH * (0.75 + 0.25 * Math.sin(i * 1.7 + 1));
      var rect = document.createElementNS(NS, "rect");
      rect.setAttribute("x", x); rect.setAttribute("y", y + barH - bh);
      rect.setAttribute("width", bw); rect.setAttribute("height", bh);
      rect.setAttribute("rx", "7");
      rect.setAttribute("fill", "url(#taskGrad)");
      rect.setAttribute("opacity", String(0.55 + 0.4 * (i + 1) / sprints));
      svg.appendChild(rect);
      var t = document.createElementNS(NS, "text");
      t.setAttribute("x", x + bw / 2); t.setAttribute("y", y + barH + 16);
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("fill", "#64748b"); t.setAttribute("font-size", "10");
      t.setAttribute("font-family", "JetBrains Mono, monospace");
      t.textContent = "S" + (i + 1);
      svg.appendChild(t);
    }

    var dim = document.createElementNS(NS, "line");
    dim.setAttribute("x1", pad); dim.setAttribute("y1", h - 22);
    dim.setAttribute("x2", w - pad); dim.setAttribute("y2", h - 22);
    dim.setAttribute("stroke", "#3b4a66"); dim.setAttribute("stroke-width", "1");
    svg.appendChild(dim);
    var dimT = document.createElementNS(NS, "text");
    dimT.setAttribute("x", w / 2); dimT.setAttribute("y", h - 10);
    dimT.setAttribute("text-anchor", "middle");
    dimT.setAttribute("fill", "#64748b"); dimT.setAttribute("font-size", "11");
    dimT.setAttribute("font-family", "JetBrains Mono, monospace");
    dimT.textContent = "команда: " + team + " розробник(и)";
    svg.appendChild(dimT);
  }

  function calc() {
    var team = parseInt(els["quote-team"].value, 10) || 1;
    var complexity = parseInt(els["quote-complexity"].value, 10) || 1;
    els["quote-team-val"].textContent = team + " розробник" + (team > 1 ? "и" : "");
    els["quote-complexity-val"].textContent = COMPLEXITY_NAMES[complexity];

    var id = ++requestId;
    setStatus("Розрахунок на сервері…", "status-wait");

    fetch("/api/calc/quote?" + new URLSearchParams({ ptype: ptype, team: String(team), complexity: String(complexity) }))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (id !== requestId || !d.ok) return;
        drawTimeline(d.weeks, team);
        els["res-cost"].textContent = d.cost.toLocaleString("uk-UA") + " грн";
        els["res-weeks"].textContent = d.weeks + " тиж";
        els["res-hours"].textContent = d.hours.toLocaleString("uk-UA");
        els["res-support"].textContent = d.support_month.toLocaleString("uk-UA") + " грн";
        els["res-price"].textContent = "від " + d.from_price.toLocaleString("uk-UA") + " грн";
        setStatus(d.type_label + " · " + d.complexity_label + " · готово до релізу", "status-ok");
      })
      .catch(function () {
        if (id !== requestId) return;
        setStatus("Офлайн-режим: сервіс недоступний", "status-fail");
        ["res-cost", "res-weeks", "res-hours", "res-support", "res-price"].forEach(function (k) {
          els[k].textContent = "—";
        });
      });
  }

  els["quote-type"].addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    els["quote-type"].querySelectorAll("button").forEach(function (b) { b.classList.remove("active"); });
    btn.classList.add("active");
    ptype = btn.getAttribute("data-type");
    calc();
  });

  els["quote-team"].addEventListener("input", calc);
  els["quote-complexity"].addEventListener("input", calc);

  var revealO = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) { calc(); revealO.unobserve(en.target); }
    });
  }, { threshold: 0.3 });
  revealO.observe(els["quote-svg"]);
})();
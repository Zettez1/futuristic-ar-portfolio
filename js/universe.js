(function () {
  "use strict";

  var TRAIN = document.getElementById("uni-train-canvas");
  var LIFE = document.getElementById("uni-life-canvas");
  var CHART = document.getElementById("uni-chart");
  if (!TRAIN || !LIFE || !CHART) return;

  var W = 800, HH = 500;
  var DPR = Math.min(window.devicePixelRatio || 1, 2);
  [TRAIN, LIFE].forEach(function (c) {
    c.width = W * DPR; c.height = HH * DPR;
    c.getContext("2d").setTransform(DPR, 0, 0, DPR, 0, 0);
  });
  CHART.width = 800 * DPR; CHART.height = 56 * DPR;
  cctx = CHART.getContext("2d"); cctx.setTransform(DPR, 0, 0, DPR, 0, 0);

  var WALLS = [
    { x: 0, y: 0, w: W, h: 8 }, { x: 0, y: HH - 8, w: W, h: 8 },
    { x: 0, y: 0, w: 8, h: HH }, { x: W - 8, y: 0, w: 8, h: HH },
    { x: 240, y: 130, w: 100, h: 8 }, { x: 470, y: 280, w: 150, h: 8 },
    { x: 360, y: 70, w: 8, h: 120 }, { x: 130, y: 300, w: 8, h: 110 }
  ];

  var IN = 12, HID = 10, OUT = 2;
  var W1 = IN * HID, B1 = HID, W2 = HID * OUT, B2 = OUT, GENOME = W1 + B1 + W2 + B2;

  function randGauss() {
    var u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
  function newBrain() {
    var b = new Float32Array(GENOME);
    for (var i = 0; i < GENOME; i++) b[i] = randGauss() * 0.4;
    return b;
  }
  function cloneBrain(b) { return new Float32Array(b); }
  function crossover(a, b) {
    var c = new Float32Array(GENOME);
    for (var i = 0; i < GENOME; i++) c[i] = Math.random() < 0.5 ? a[i] : b[i];
    return mutate(c);
  }
  function mutate(b) {
    var c = new Float32Array(b);
    for (var i = 0; i < GENOME; i++) {
      var r = Math.random();
      if (r < 0.16) c[i] += randGauss() * 0.35;
      else if (r < 0.185) c[i] = randGauss() * 0.4;
    }
    return c;
  }
  function forward(b, x) {
    var h = new Float32Array(HID), o = new Float32Array(OUT);
    var k = 0;
    for (var j = 0; j < HID; j++) {
      var s = b[W1 + j];
      for (var i = 0; i < IN; i++) s += x[i] * b[k++];
      h[j] = Math.tanh(s);
    }
    for (j = 0; j < OUT; j++) {
      var t = b[W1 + B1 + W2 + j];
      for (i = 0; i < HID; i++) t += h[i] * b[W1 + B1 + i * OUT + j];
      o[j] = Math.tanh(t);
    }
    return o;
  }

  function inWall(px, py, r) {
    for (var i = 0; i < WALLS.length; i++) {
      var w = WALLS[i];
      if (px + r >= w.x && px - r <= w.x + w.w && py + r >= w.y && py - r <= w.y + w.h) return true;
    }
    return false;
  }
  function freeSpot() {
    for (var t = 0; t < 60; t++) {
      var x = 20 + Math.random() * (W - 40), y = 20 + Math.random() * (HH - 40);
      if (!inWall(x, y, 10)) return { x: x, y: y };
    }
    return { x: W / 2, y: HH / 2 };
  }
  var food = [];
  function resetFood() {
    food = [];
    for (var i = 0; i < 10; i++) {
      var p = freeSpot();
      food.push({ x: p.x, y: p.y, r: 5 + Math.random() * 2 });
    }
  }
  function spawnOneFood() {
    var p = freeSpot();
    food.push({ x: p.x, y: p.y, r: 5 + Math.random() * 2 });
  }
  function march(bot, ang, range) {
    var sx = Math.cos(ang) * 5, sy = Math.sin(ang) * 5, d = 0;
    var x = bot.x, y = bot.y;
    while (d < range) {
      x += sx; y += sy; d += 5;
      if (inWall(x, y, 3)) break;
    }
    return d;
  }
  function sense(bot) {
    var x = new Float32Array(IN);
    var s0 = -64, span = 128;
    for (var k = 0; k < 8; k++) {
      var a = bot.a + (s0 + span * k / 7) * Math.PI / 180;
      x[k] = march(bot, a, 130) / 130;
    }
    var bestF = -1, bd = 1e9;
    for (i = 0; i < food.length; i++) {
      var dx = food[i].x - bot.x, dy = food[i].y - bot.y;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d < bd) { bd = d; bestF = i; }
    }
    x[8] = Math.min(1, bd / 420);
    if (bestF >= 0) {
      var da = Math.atan2(food[bestF].y - bot.y, food[bestF].x - bot.x) - bot.a;
      x[9] = Math.cos(da); x[10] = Math.sin(da);
    } else { x[9] = 0; x[10] = 0; }
    x[11] = 1;
    return x;
  }

  var POP = 56, STEPS = 3200, ELITE = 6;
  var bots = [], gen = 1, step = 0;
  var histMax = [], histAvg = [];
  var champion = newBrain(), champFit = -1, champGen = 0;

  function makeBot() {
    var p = freeSpot();
    return {
      brain: newBrain(), x: p.x, y: p.y, a: Math.random() * Math.PI * 2,
      fit: 0, eaten: 0, dist: 0, wallT: 0
    };
  }
  function respawnBot(b, brain) {
    var p = freeSpot();
    b.brain = brain; b.x = p.x; b.y = p.y;
    b.a = Math.random() * Math.PI * 2;
    b.fit = 0; b.eaten = 0; b.dist = 0; b.wallT = 0;
  }
  function resetEvolution() {
    bots = [];
    for (var i = 0; i < POP; i++) bots.push(makeBot());
    gen = 1; step = 0;
    histMax = []; histAvg = [];
    champion = newBrain(); champFit = -1; champGen = 0;
    resetFood();
    lifeReset(true);
  }
  function stepBot(b, dt) {
    var o = forward(b.brain, sense(b));
    var L = (o[0] + 1) * 60, R = (o[1] + 1) * 60;
    var vx = Math.cos(b.a) * (L + R) / 2, vy = Math.sin(b.a) * (L + R) / 2;
    var nx = b.x + vx * dt, ny = b.y + vy * dt;
    b.a += (R - L) * dt / 30;
    b.dist += Math.sqrt(vx * vx + vy * vy) * dt;
    if (!inWall(nx, ny, 5)) { b.x = nx; b.y = ny; }
    else { b.wallT++; b.fit -= 1.5; }
    b.fit += Math.sqrt(vx * vx + vy * vy) * dt * 0.02;
    for (var i = 0; i < food.length; i++) {
      var dx = food[i].x - b.x, dy = food[i].y - b.y;
      if (dx * dx + dy * dy < 110) {
        food.splice(i, 1);
        b.eaten++; b.fit += 90;
        spawnOneFood();
        break;
      }
    }
  }
  function nextGeneration() {
    bots.sort(function (a, b) { return b.fit - a.fit; });
    var maxF = bots[0].fit, sum = 0;
    for (var i = 0; i < POP; i++) sum += bots[i].fit;
    histMax.push(maxF); histAvg.push(sum / POP);
    if (histMax.length > 200) { histMax.shift(); histAvg.shift(); }
    if (bots[0].fit > champFit) {
      champFit = bots[0].fit;
      champion = cloneBrain(bots[0].brain);
      champGen = gen;
      lifeReset(false);
    }
    var elite = [];
    for (i = 0; i < ELITE; i++) respawnBot(bots[i], cloneBrain(bots[i].brain));
    function tournament() {
      var best = bots[0], bestFit = -1e9;
      for (var k = 0; k < 5; k++) {
        var c = bots[(Math.random() * POP) | 0];
        if (c.fit > bestFit) { bestFit = c.fit; best = c; }
      }
      return best;
    }
    for (i = ELITE; i < POP; i++) {
      var p1 = tournament(), p2 = tournament();
      respawnBot(bots[i], crossover(p1.brain, p2.brain));
    }
    step = 0; gen++;
    resetFood();
  }

  var lifeBot = null, lifeSteps = 0, lifeEaten = 0, lifeWallT = 0, lifeDist = 0;
  var lifeFood = [], lifePath = [], lifeCover = new Uint8Array(24 * 16), lifeCovered = 0;
  function lifeReset(isFirst) {
    var p = freeSpot();
    lifeBot = { brain: cloneBrain(champion), x: p.x, y: p.y, a: Math.random() * Math.PI * 2 };
    lifeFood = [];
    for (var i = 0; i < 10; i++) {
      var q = freeSpot();
      lifeFood.push({ x: q.x, y: q.y, r: 5 + Math.random() * 2 });
    }
    lifeSteps = 0; lifeEaten = 0; lifeWallT = 0; lifeDist = 0;
    lifePath = []; lifeCover = new Uint8Array(24 * 16); lifeCovered = 0;
    lifeCoveredTel = 0;
    document.getElementById("uni-life-gen").textContent = champGen || "—";
    var f = document.getElementById("uni-life-gen");
    f.classList.remove("uni-flash"); void f.offsetWidth; f.classList.add("uni-flash");
    renderSkills();
  }
  function lifeStep(dt) {
    var o = forward(lifeBot.brain, senseLife());
    var L = (o[0] + 1) * 60, R = (o[1] + 1) * 60;
    var vx = Math.cos(lifeBot.a) * (L + R) / 2, vy = Math.sin(lifeBot.a) * (L + R) / 2;
    var nx = lifeBot.x + vx * dt, ny = lifeBot.y + vy * dt;
    lifeBot.a += (R - L) * dt / 30;
    lifeDist += Math.sqrt(vx * vx + vy * vy) * dt;
    if (!inWall(nx, ny, 5)) { lifeBot.x = nx; lifeBot.y = ny; }
    else lifeWallT++;
    lifeSteps++;
    if (lifeSteps % 30 === 0) {
      lifePath.push({ x: lifeBot.x, y: lifeBot.y });
      if (lifePath.length > 46) lifePath.shift();
      var cx = Math.max(0, Math.min(23, (lifeBot.x / W * 24) | 0));
      var cy = Math.max(0, Math.min(15, (lifeBot.y / HH * 16) | 0));
      if (!lifeCover[cx + cy * 24]) { lifeCover[cx + cy * 24] = 1; lifeCovered++; }
    }
    for (var i = 0; i < lifeFood.length; i++) {
      var dx = lifeFood[i].x - lifeBot.x, dy = lifeFood[i].y - lifeBot.y;
      if (dx * dx + dy * dy < 110) {
        lifeFood.splice(i, 1);
        lifeEaten++;
        var p = freeSpot();
        lifeFood.push({ x: p.x, y: p.y, r: 5 + Math.random() * 2 });
        break;
      }
    }
    document.getElementById("uni-life-food").textContent = lifeEaten;
    updateLifeGen();
  }
  function senseLife() {
    var x = new Float32Array(IN);
    var s0 = -64, span = 128;
    for (var k = 0; k < 8; k++) {
      var a = lifeBot.a + (s0 + span * k / 7) * Math.PI / 180;
      x[k] = march(lifeBot, a, 130) / 130;
    }
    var bestF = -1, bd = 1e9;
    for (i = 0; i < lifeFood.length; i++) {
      var dx = lifeFood[i].x - lifeBot.x, dy = lifeFood[i].y - lifeBot.y;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d < bd) { bd = d; bestF = i; }
    }
    x[8] = Math.min(1, bd / 420);
    if (bestF >= 0) {
      var da = Math.atan2(lifeFood[bestF].y - lifeBot.y, lifeFood[bestF].x - lifeBot.x) - lifeBot.a;
      x[9] = Math.cos(da); x[10] = Math.sin(da);
    }
    x[11] = 1;
    return x;
  }
  function updateLifeGen() {
    var genEl = document.getElementById("uni-life-gen");
    if (genEl.dataset.g !== String(champGen)) {
      genEl.dataset.g = String(champGen);
      genEl.classList.remove("uni-flash"); void genEl.offsetWidth; genEl.classList.add("uni-flash");
      renderSkills();
    }
  }
  function renderSkills() {
    var box = document.getElementById("uni-skills");
    var skills = [];
    if (lifeEaten > 0) skills.push("знаходить і збирає їжу");
    if (lifeSteps > 400 && lifeWallT / lifeSteps < 0.12) skills.push("обходить стіни та перешкоди");
    if (lifeSteps > 300 && lifeCovered / 384 > 0.25) skills.push("досліджує територію");
    if (lifeSteps > 400 && lifeDist / lifeSteps > 0.35) skills.push("тримає напрямок та швидкість");
    if (lifeSteps > 700 && lifeEaten * 1000 / lifeSteps > 1.4) skills.push("полює на їжу цілеспрямовано");
    box.innerHTML = "";
    if (!skills.length) {
      var ghost = document.createElement("div");
      ghost.className = "uni-skill";
      ghost.innerHTML = "<span class='text-slate-500'>◌</span><span class='text-slate-400'>мозок адаптується — навички з'являться тут</span>";
      box.appendChild(ghost);
      return;
    }
    skills.forEach(function (s) {
      var d = document.createElement("div");
      d.className = "uni-skill uni-skill-in";
      d.innerHTML = "<span class='uni-skill-ok'>✓</span><span>" + s + "</span>";
      box.appendChild(d);
    });
  }

  function drawWorld(ctx, foods, isLife) {
    ctx.clearRect(0, 0, W, HH);
    ctx.fillStyle = "#05070f";
    ctx.fillRect(0, 0, W, HH);
    ctx.strokeStyle = "rgba(148,163,184,0.055)";
    ctx.lineWidth = 1;
    for (var gx = 0; gx <= W; gx += 40) { ctx.beginPath(); ctx.moveTo(gx + 0.5, 0); ctx.lineTo(gx + 0.5, HH); ctx.stroke(); }
    for (var gy = 0; gy <= HH; gy += 40) { ctx.beginPath(); ctx.moveTo(0, gy + 0.5); ctx.lineTo(W, gy + 0.5); ctx.stroke(); }
    ctx.fillStyle = "rgba(139,92,246,0.55)";
    for (i = 0; i < WALLS.length; i++) ctx.fillRect(WALLS[i].x, WALLS[i].y, WALLS[i].w, WALLS[i].h);
    ctx.save();
    ctx.shadowColor = "rgba(52,211,153,0.8)"; ctx.shadowBlur = 6;
    ctx.fillStyle = "#34d399";
    for (i = 0; i < foods.length; i++) {
      ctx.beginPath(); ctx.arc(foods[i].x, foods[i].y, foods[i].r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
  }
  function drawBotShape(ctx, b, col, glow) {
    ctx.save();
    ctx.translate(b.x, b.y);
    ctx.rotate(b.a);
    if (glow) { ctx.shadowColor = col; ctx.shadowBlur = 9; }
    ctx.fillStyle = col;
    ctx.fillRect(-6, -4, 12, 8);
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fillRect(4, -1.5, 3.5, 3);
    ctx.restore();
  }
  function drawRays(ctx, b, col) {
    ctx.strokeStyle = col;
    ctx.lineWidth = 1;
    for (var k = 0; k < 8; k++) {
      var a = b.a + (-64 + 128 * k / 7) * Math.PI / 180;
      var d = march(b, a, 130);
      ctx.beginPath();
      ctx.moveTo(b.x, b.y);
      ctx.lineTo(b.x + Math.cos(a) * d, b.y + Math.sin(a) * d);
      ctx.stroke();
    }
  }

  var bestIdx = 0;
  function drawTrain() {
    drawWorld(tctx, food, false);
    for (var i = 0; i < POP; i++) {
      if (i === bestIdx) continue;
      drawBotShape(tctx, bots[i], "rgba(100,116,139,0.75)", false);
    }
    var b = bots[bestIdx];
    drawRays(tctx, b, "rgba(34,211,238,0.35)");
    drawBotShape(tctx, b, "#22d3ee", true);
  }
  function drawLife() {
    drawWorld(lctx, lifeFood, true);
    if (lifePath.length > 1) {
      ctx = lctx;
      ctx.strokeStyle = "rgba(34,211,238,0.3)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(lifePath[0].x, lifePath[0].y);
      for (var i = 1; i < lifePath.length; i++) ctx.lineTo(lifePath[i].x, lifePath[i].y);
      ctx.stroke();
    }
    drawRays(lctx, lifeBot, "rgba(34,211,238,0.4)");
    drawBotShape(lctx, lifeBot, "#22d3ee", true);
  }

  function drawChart() {
    var w = 800, h = 56;
    cctx.clearRect(0, 0, w, h);
    cctx.fillStyle = "#05070f";
    cctx.fillRect(0, 0, w, h);
    if (!histMax.length) return;
    var m = 0;
    for (var i = 0; i < histMax.length; i++) if (histMax[i] > m) m = histMax[i];
    if (m <= 0) m = 1;
    function line(arr, col) {
      cctx.strokeStyle = col; cctx.lineWidth = 1.5;
      cctx.beginPath();
      for (var i = 0; i < arr.length; i++) {
        var x = i / (histMax.length - 1 || 1) * w;
        var y = h - 4 - (arr[i] / m) * (h - 8);
        if (i === 0) cctx.moveTo(x, y); else cctx.lineTo(x, y);
      }
      cctx.stroke();
    }
    line(histMax, "#22d3ee");
    line(histAvg, "rgba(148,163,184,0.6)");
    cctx.strokeStyle = "rgba(148,163,184,0.12)";
    for (var gy = 0; gy < h; gy += 14) { cctx.beginPath(); cctx.moveTo(0, gy + 0.5); cctx.lineTo(w, gy + 0.5); cctx.stroke(); }
  }

  function updateUI() {
    document.getElementById("uni-gen").textContent = gen;
    var best = bots[bestIdx] ? bots[bestIdx].fit : 0;
    document.getElementById("uni-best").textContent = Math.round(best);
    var sum = 0;
    for (var i = 0; i < POP; i++) sum += bots[i].fit;
    document.getElementById("uni-avg").textContent = Math.round(sum / POP);
    document.getElementById("uni-chart-seen").textContent = histMax.length;
  }

  var trainOn = true, visible = true;
  var speed = 10;
  document.getElementById("uni-toggle").addEventListener("click", function () {
    trainOn = !trainOn;
    this.textContent = trainOn ? "Пауза" : "Продовжити";
    document.getElementById("uni-train-status").textContent = trainOn ? "еволюція йде" : "на паузі";
  });
  document.getElementById("uni-reset").addEventListener("click", function () {
    resetEvolution();
  });
  document.getElementById("uni-speed").addEventListener("change", function () {
    speed = parseInt(this.value, 10) || 10;
  });

  var sec = document.getElementById("universe");
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (es) {
      visible = es[0].isIntersecting;
    }, { rootMargin: "200px" }).observe(sec);
  }

  resetEvolution();
  var last = performance.now();
  function frame(now) {
    var dt = Math.min(0.033, (now - last) / 1000);
    last = now;
    if (visible) {
      if (trainOn) {
        for (var s = 0; s < speed; s++) {
          bestIdx = 0;
          var bf = -1e9;
          for (var i = 0; i < POP; i++) {
            stepBot(bots[i], dt);
            if (bots[i].fit > bf) { bf = bots[i].fit; bestIdx = i; }
          }
          step++;
          if (step >= STEPS) nextGeneration();
        }
        drawTrain();
        drawChart();
        updateUI();
      } else {
        drawTrain();
      }
      for (var s2 = 0; s2 < speed; s2++) lifeStep(dt);
      drawLife();
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
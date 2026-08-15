(function () {
  "use strict";

  var canvas = document.getElementById("hero-canvas");
  if (!canvas || typeof THREE === "undefined") return;

  var renderer = null, scene = null, camera = null;
  var particles, particleGeom, lines, knot;
  var mouse = { x: 0, y: 0 };
  var touch = { x: 0, y: 0, active: false };
  var clock = new THREE.Clock();
  var running = true;

  var particleCount = 1400;
  var W = 18, H = 10, D = 9;

  function rand(min, max) { return min + Math.random() * (max - min); }

  function makeGlowTexture() {
    var c = document.createElement("canvas");
    c.width = c.height = 64;
    var g = c.getContext("2d");
    var grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.4, "rgba(165,220,255,0.85)");
    grad.addColorStop(1, "rgba(120,120,255,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    var tex = new THREE.CanvasTexture(c);
    return tex;
  }

  function init() {
    scene = new THREE.Scene();

    camera = new THREE.PerspectiveCamera(65, canvas.clientWidth / canvas.clientHeight, 0.1, 200);
    camera.position.set(0, 1.4, 14);
    camera.lookAt(0, 0, 0);

    renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(canvas.clientWidth, canvas.clientHeight);

    // --- particle field ---
    particleGeom = new THREE.BufferGeometry();
    var positions = new Float32Array(particleCount * 3);
    var colors = new Float32Array(particleCount * 3);
    var c1 = new THREE.Color("#22d3ee");
    var c2 = new THREE.Color("#a78bfa");
    for (var i = 0; i < particleCount; i++) {
      positions[i * 3] = rand(-W / 2, W / 2);
      positions[i * 3 + 1] = rand(-H / 2, H / 2);
      positions[i * 3 + 2] = rand(-D / 2, D / 2);
      var tc = c1.clone().lerp(c2, rand(0, 1));
      colors[i * 3] = tc.r; colors[i * 3 + 1] = tc.g; colors[i * 3 + 2] = tc.b;
    }
    particleGeom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    particleGeom.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    var pmat = new THREE.PointsMaterial({
      size: 0.075,
      map: makeGlowTexture(),
      vertexColors: true,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });
    particles = new THREE.Points(particleGeom, pmat);
    scene.add(particles);

    // --- connecting lines ---
    var linePos = [];
    var maxDist = 1.6;
    var pos = particleGeom.attributes.position.array;
    for (var a = 0; a < particleCount; a++) {
      for (var b = a + 1; b < particleCount; b++) {
        var dx = pos[a * 3] - pos[b * 3];
        var dy = pos[a * 3 + 1] - pos[b * 3 + 1];
        var dz = pos[a * 3 + 2] - pos[b * 3 + 2];
        if (dx * dx + dy * dy + dz * dz < maxDist * maxDist) {
          linePos.push(pos[a * 3], pos[a * 3 + 1], pos[a * 3 + 2]);
          linePos.push(pos[b * 3], pos[b * 3 + 1], pos[b * 3 + 2]);
        }
      }
    }
    var lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute("position", new THREE.Float32BufferAttribute(linePos, 3));
    var lineMat = new THREE.LineBasicMaterial({ color: new THREE.Color("#4c6ef5"), transparent: true, opacity: 0.12, blending: THREE.AdditiveBlending });
    lines = new THREE.LineSegments(lineGeom, lineMat);
    scene.add(lines);

    // --- central wireframe core ---
    knot = new THREE.Mesh(
      new THREE.IcosahedronGeometry(2.1, 1),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color("#8b5cf6"),
        wireframe: true,
        transparent: true,
        opacity: 0.35
      })
    );
    knot.position.set(0, 0, 0);
    scene.add(knot);

    var ring = new THREE.Mesh(
      new THREE.TorusGeometry(3.4, 0.02, 8, 90),
      new THREE.MeshBasicMaterial({ color: new THREE.Color("#22d3ee"), transparent: true, opacity: 0.5 })
    );
    ring.rotation.x = Math.PI / 2.4;
    scene.add(ring);

    window.addEventListener("resize", onResize);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mousedown", onPointerDown);
    window.addEventListener("touchmove", onTouchMove, { passive: true });
    document.addEventListener("visibilitychange", function () {
      running = !document.hidden;
      if (running) clock.getDelta();
    });

    animate();
  }

  function onResize() {
    if (!renderer) return;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }

  function onMouseMove(e) {
    mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
    mouse.y = (e.clientY / window.innerHeight) * 2 - 1;
  }
  function onPointerDown(e) { mouse.x = (e.clientX / window.innerWidth) * 2 - 1; mouse.y = (e.clientY / window.innerHeight) * 2 - 1; }
  function onTouchMove(e) {
    var t = e.touches[0];
    mouse.x = (t.clientX / window.innerWidth) * 2 - 1;
    mouse.y = (t.clientY / window.innerHeight) * 2 - 1;
  }

  var t = 0;
  function animate() {
    requestAnimationFrame(animate);
    if (!running) return;
    var dt = clock.getDelta();
    t += dt;

    var targetX = mouse.x * 2.2;
    var targetY = -mouse.y * 1.4;
    camera.position.x += (targetX - camera.position.x) * 0.045;
    camera.position.y += (targetY - camera.position.y) * 0.045;
    camera.lookAt(0, 0, 0);

    particles.rotation.y = t * 0.02;
    lines.rotation.y = t * 0.02;
    knot.rotation.x = t * 0.16;
    knot.rotation.y = t * 0.22;
    knot.scale.setScalar(1 + 0.05 * Math.sin(t * 0.8));

    var pos = particleGeom.attributes.position.array;
    for (var i = 0; i < particleCount; i++) {
      pos[i * 3 + 1] += Math.sin(t * 0.4 + i * 0.35) * 0.0008;
    }
    particleGeom.attributes.position.needsUpdate = true;

    renderer.render(scene, camera);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
(function () {
  var logo = document.querySelector('#navbar .logo-cube');
  var link = document.querySelector('#navbar a[href="#top"]');
  if (!logo || !link) return;

  var dragging = false;
  var moved = false;
  var startX = 0;
  var startY = 0;

  function applyTransform(angle, depth) {
    logo.style.transform =
      'perspective(600px) rotateY(' + angle.toFixed(2) + 'deg) translateZ(' + depth.toFixed(2) + 'px)';
  }

  function scrollTop(e) {
    e.preventDefault();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  }

  link.addEventListener('click', function (e) {
    if (moved) {
      e.preventDefault();
      setTimeout(function () { moved = false; }, 0);
      return;
    }
    scrollTop(e);
  });

  logo.addEventListener('pointerdown', function (e) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    e.preventDefault();
    dragging = true;
    moved = false;
    startX = e.clientX;
    startY = e.clientY;
    try { logo.setPointerCapture(e.pointerId); } catch (err) {}
    logo.classList.add('logo-dragging');
  });

  logo.addEventListener('dragstart', function (e) { e.preventDefault(); });
  link.addEventListener('dragstart', function (e) { e.preventDefault(); });

  logo.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    var dx = e.clientX - startX;
    var dy = e.clientY - startY;
    if (e.pointerType === 'touch') {
      if (Math.abs(dx) > Math.abs(dy)) {
        moved = true;
        applyTransform(dx * 0.8, 0);
      }
      return;
    }
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
    applyTransform(dx * 0.8, Math.max(-70, Math.min(70, dy * 0.8)));
  });

  function endDrag(e) {
    if (!dragging) return;
    var wasUp = e.type === 'pointerup';
    dragging = false;
    logo.classList.remove('logo-dragging');
    logo.style.transition = 'transform .5s cubic-bezier(.22,1,.36,1)';
    applyTransform(0, 0);
    setTimeout(function () {
      logo.style.transition = '';
    }, 520);
    if (wasUp && !moved) {
      e.preventDefault();
      scrollTop(e);
    }
  }

  logo.addEventListener('pointerup', endDrag);
  logo.addEventListener('pointercancel', endDrag);
})();
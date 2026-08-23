// Живой фон «из будущего»: сеть частиц, реагирующая на курсор
(function () {
  'use strict';

  var canvas = document.getElementById('bg-particles');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  var particles = [];
  var mouse = { x: -9999, y: -9999 };
  var LINK_DIST = 130;
  var MOUSE_DIST = 190;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }

  function createParticles() {
    var count = Math.min(90, Math.floor(window.innerWidth / 16));
    particles = [];
    for (var i = 0; i < count; i++) {
      particles.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx: (Math.random() - .5) * .45,
        vy: (Math.random() - .5) * .45,
        r: Math.random() * 1.6 + .6
      });
    }
  }

  function step() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      p.x += p.vx; p.y += p.vy;

      // Отталкивание от курсора
      var dxm = p.x - mouse.x, dym = p.y - mouse.y;
      var dm = Math.sqrt(dxm * dxm + dym * dym);
      if (dm < MOUSE_DIST && dm > 0) {
        p.x += (dxm / dm) * 1.1;
        p.y += (dym / dm) * 1.1;
      }

      // Заворачивание за края
      if (p.x < -20) p.x = canvas.width + 20;
      if (p.x > canvas.width + 20) p.x = -20;
      if (p.y < -20) p.y = canvas.height + 20;
      if (p.y > canvas.height + 20) p.y = -20;

      // Точка
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(139, 92, 246, .55)';
      ctx.fill();

      // Линии между близкими точками
      for (var j = i + 1; j < particles.length; j++) {
        var q = particles[j];
        var dx = p.x - q.x, dy = p.y - q.y;
        var d2 = dx * dx + dy * dy;
        if (d2 < LINK_DIST * LINK_DIST) {
          var alpha = (1 - Math.sqrt(d2) / LINK_DIST) * .22;
          ctx.strokeStyle = 'rgba(99, 102, 241, ' + alpha.toFixed(3) + ')';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.stroke();
        }
      }

      // Связь с курсором — акцентная
      if (dm < MOUSE_DIST) {
        ctx.strokeStyle = 'rgba(34, 211, 238, ' + ((1 - dm / MOUSE_DIST) * .35).toFixed(3) + ')';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(mouse.x, mouse.y);
        ctx.stroke();
      }
    }
    requestAnimationFrame(step);
  }

  window.addEventListener('resize', function () { resize(); createParticles(); });
  document.addEventListener('mousemove', function (e) { mouse.x = e.clientX; mouse.y = e.clientY; });
  document.addEventListener('mouseleave', function () { mouse.x = -9999; mouse.y = -9999; });

  resize();
  createParticles();
  requestAnimationFrame(step);
})();

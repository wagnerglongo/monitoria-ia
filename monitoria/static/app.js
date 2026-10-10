// Melhorias progressivas do painel (a CSP bloqueia script e estilo inline).
(function () {
  'use strict';

  // Barras: largura vem de data-largura (0 a 100).
  document.querySelectorAll('[data-largura]').forEach(function (barra) {
    var valor = Math.max(0, Math.min(100, parseFloat(barra.getAttribute('data-largura')) || 0));
    barra.style.width = valor + '%';
  });

  // Linhas de tabela clicáveis.
  document.querySelectorAll('tr[data-href]').forEach(function (linha) {
    linha.addEventListener('click', function (evento) {
      if (evento.target.closest('a')) return;
      window.location.href = linha.getAttribute('data-href');
    });
  });

  // Clicar na evidência leva à fala correspondente na transcrição.
  document.querySelectorAll('a.evidencia[data-criterio]').forEach(function (link) {
    link.addEventListener('click', function (evento) {
      evento.preventDefault();
      var id = link.getAttribute('data-criterio');
      document.querySelectorAll('.fala.destaque').forEach(function (f) { f.classList.remove('destaque'); });
      var fala = Array.prototype.find.call(document.querySelectorAll('.fala[data-marcas]'), function (f) {
        return f.getAttribute('data-marcas').split(' ').indexOf(id) !== -1;
      });
      if (fala) {
        fala.classList.add('destaque');
        fala.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });
  });

  // Ligação na fila: recarrega até a avaliação terminar.
  var aviso = document.querySelector('[data-recarregar]');
  if (aviso) {
    setTimeout(function () { window.location.reload(); }, (parseInt(aviso.getAttribute('data-recarregar'), 10) || 5) * 1000);
  }
})();

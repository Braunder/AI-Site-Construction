// Страница проекта: чат-редактирование, перегенерация, удаление, опрос статуса
(function () {
  'use strict';

  var projectId = window.PROJECT_ID;
  var iframe = document.getElementById('preview');
  var chatForm = document.getElementById('chat-form');
  var chatInput = document.getElementById('chat-input');
  var chatSend = document.getElementById('chat-send');
  var thinking = document.getElementById('chat-thinking');
  var regenBtn = document.getElementById('regen-btn');
  var deleteBtn = document.getElementById('delete-btn');
  var errorAlert = document.getElementById('error-alert');
  var pollTimer = null;

  function refreshPreview() {
    iframe.src = '/projects/' + projectId + '/preview?t=' + Date.now();
  }

  function setBusy(busy) {
    chatSend.disabled = busy;
    chatInput.disabled = busy;
    regenBtn.disabled = busy;
    thinking.classList.toggle('d-none', !busy);
  }

  function startPolling() {
    if (pollTimer) return;
    setBusy(true);
    pollTimer = setInterval(async function () {
      try {
        var resp = await fetch('/api/projects/' + projectId + '/status');
        var data = await resp.json();
        if (data.status === 'done' || data.status === 'error') {
          clearInterval(pollTimer);
          pollTimer = null;
          setBusy(false);
          if (data.status === 'error') {
            errorAlert.textContent = data.error || 'Неизвестная ошибка';
            errorAlert.classList.remove('d-none');
          } else {
            errorAlert.classList.add('d-none');
            window.location.reload(); // обновить историю чата и превью
          }
        }
      } catch (e) { /* повторяем */ }
    }, 3000);
  }

  // Если страница открыта во время обработки — продолжаем опрос
  if (window.PROJECT_STATUS === 'processing' || window.PROJECT_STATUS === 'pending') {
    startPolling();
  }

  chatForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    var instruction = chatInput.value.trim();
    if (!instruction) return;
    try {
      var resp = await fetch('/api/projects/' + projectId + '/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: instruction })
      });
      if (!resp.ok) {
        var err = await resp.json().catch(function () { return {}; });
        throw new Error(err.detail || ('HTTP ' + resp.status));
      }
      chatInput.value = '';
      startPolling();
    } catch (err) {
      alert('Ошибка: ' + err.message);
    }
  });

  regenBtn.addEventListener('click', async function () {
    if (!confirm('Полностью перегенерировать сайт с теми же параметрами?')) return;
    var resp = await fetch('/api/projects/' + projectId + '/regenerate', { method: 'POST' });
    if (resp.ok) startPolling();
    else alert('Не удалось запустить перегенерацию');
  });

  deleteBtn.addEventListener('click', async function () {
    if (!confirm('Удалить проект безвозвратно?')) return;
    var resp = await fetch('/api/projects/' + projectId, { method: 'DELETE' });
    if (resp.ok) window.location.href = '/projects';
    else alert('Не удалось удалить проект');
  });
})();

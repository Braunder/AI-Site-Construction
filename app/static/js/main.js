// Форма генерации: drag-and-drop, пресеты, валидация, отправка + опрос статуса
(function () {
  'use strict';

  var form = document.getElementById('gen-form');
  var promptEl = document.getElementById('prompt');
  var dropZone = document.getElementById('drop-zone');
  var fileInput = document.getElementById('image');
  var fileName = document.getElementById('file-name');
  var filePreview = document.getElementById('file-preview');
  var overlay = document.getElementById('loading-overlay');
  var loadingText = document.getElementById('loading-text');
  var MAX_BYTES = 5 * 1024 * 1024;

  // --- Пресеты и примеры ---
  document.querySelectorAll('.preset-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      promptEl.value = btn.dataset.prompt;
      promptEl.classList.remove('is-invalid');
    });
  });
  var EXAMPLES = {
    'пекарня': 'Сайт для семейной пекарни: ассортимент хлеба и выпечки, история пекарни, доставка, контакты и часы работы.',
    'фотограф': 'Портфолио свадебного фотографа: галерея работ, пакеты услуг с ценами, отзывы, форма бронирования даты.',
    'IT-курсы': 'Лендинг школы программирования: направления курсов, формат обучения, преподаватели, стоимость, форма заявки.',
    'стоматология': 'Сайт стоматологической клиники: услуги и цены, врачи, запись на приём, адрес с картой-заглушкой.'
  };
  document.querySelectorAll('.example-chip').forEach(function (chip) {
    chip.addEventListener('click', function (e) {
      e.preventDefault();
      promptEl.value = EXAMPLES[chip.textContent.trim()] || '';
      promptEl.classList.remove('is-invalid');
    });
  });

  // --- Drag-and-drop ---
  dropZone.addEventListener('click', function () { fileInput.click(); });
  ['dragenter', 'dragover'].forEach(function (ev) {
    dropZone.addEventListener(ev, function (e) { e.preventDefault(); dropZone.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    dropZone.addEventListener(ev, function (e) { e.preventDefault(); dropZone.classList.remove('dragover'); });
  });
  dropZone.addEventListener('drop', function (e) {
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      showFile();
    }
  });
  fileInput.addEventListener('change', showFile);

  function showFile() {
    var f = fileInput.files[0];
    if (!f) { fileName.textContent = 'файл не выбран'; filePreview.classList.add('d-none'); return; }
    fileName.textContent = f.name + ' (' + Math.round(f.size / 1024) + ' КБ)';
    if (f.type.indexOf('image/') === 0) {
      filePreview.src = URL.createObjectURL(f);
      filePreview.classList.remove('d-none');
    }
    dropZone.classList.remove('is-invalid');
  }

  // --- Валидация ---
  function validate() {
    var ok = true;
    if (!promptEl.value.trim() || promptEl.value.length > 5000) {
      promptEl.classList.add('is-invalid'); ok = false;
    } else {
      promptEl.classList.remove('is-invalid');
    }
    var f = fileInput.files[0];
    if (f && (['image/jpeg', 'image/png'].indexOf(f.type) === -1 || f.size > MAX_BYTES)) {
      dropZone.classList.add('is-invalid'); ok = false;
    } else {
      dropZone.classList.remove('is-invalid');
    }
    return ok;
  }

  // --- Отправка + опрос статуса ---
  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    if (!validate()) return;
    overlay.classList.remove('d-none');
    loadingText.textContent = 'Отправка задания…';
    try {
      var fd = new FormData(form);
      var resp = await fetch('/api/projects', { method: 'POST', body: fd });
      if (!resp.ok) {
        var err = await resp.json().catch(function () { return {}; });
        throw new Error(err.detail || ('HTTP ' + resp.status));
      }
      var data = await resp.json();
      loadingText.textContent = 'Генерация сайта… это может занять несколько минут';
      pollStatus(data.id);
    } catch (err) {
      overlay.classList.add('d-none');
      alert('Ошибка: ' + err.message);
    }
  });

  function pollStatus(id) {
    var timer = setInterval(async function () {
      try {
        var resp = await fetch('/api/projects/' + id + '/status');
        var data = await resp.json();
        if (data.status === 'done') {
          clearInterval(timer);
          window.location.href = '/projects/' + id;
        } else if (data.status === 'error') {
          clearInterval(timer);
          overlay.classList.add('d-none');
          alert('Генерация не удалась: ' + (data.error || 'неизвестная ошибка'));
        }
      } catch (e) { /* сеть моргнула — пробуем дальше */ }
    }, 3000);
  }
})();

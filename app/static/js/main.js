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
  var submitting = false; // защита от двойного срабатывания submit
  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    if (submitting) return; // повторный клик/Enter игнорируем
    if (!validate()) return;
    submitting = true;
    var submitBtn = document.getElementById('submit-btn');
    if (submitBtn) submitBtn.disabled = true;
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
      submitting = false; // позволяем повторить попытку после ошибки
      if (submitBtn) submitBtn.disabled = false;
      overlay.classList.add('d-none');
      alert('Ошибка: ' + err.message);
    }
  });

  // --- AI-прогресс: анимация этапов генерации ---
  var stepEls = document.querySelectorAll('#ai-steps li');
  var stepTimer = null;
  var currentStep = 0;

  function markStep(idx, cls) {
    if (!stepEls[idx]) return;
    stepEls[idx].classList.remove('active', 'done');
    if (cls) stepEls[idx].classList.add(cls);
  }

  function startAiSteps() {
    currentStep = 0;
    markStep(0, 'active');
    // Этапы 0–3 идут по времени (генерация занимает минуты), финал — по статусу
    var delays = [0, 12000, 30000, 70000];
    stepTimer = setTimeout(function advance() {
      markStep(currentStep, 'done');
      currentStep += 1;
      if (currentStep < stepEls.length - 1) {
        markStep(currentStep, 'active');
        stepTimer = setTimeout(advance, delays[currentStep] - delays[currentStep - 1] || 15000);
      }
    }, 100);
  }

  function finishAiSteps() {
    clearTimeout(stepTimer);
    for (var i = 0; i < stepEls.length; i++) {
      markStep(i, i < stepEls.length - 1 ? 'done' : 'done');
    }
  }

  function pollStatus(id) {
    startAiSteps();
    var timer = setInterval(async function () {
      try {
        var resp = await fetch('/api/projects/' + id + '/status');
        var data = await resp.json();
        if (data.status === 'done') {
          clearInterval(timer);
          loadingText.textContent = 'Готово! Открываем ваш сайт…';
          finishAiSteps();
          setTimeout(function () { window.location.href = '/projects/' + id; }, 900);
        } else if (data.status === 'error') {
          clearInterval(timer);
          overlay.classList.add('d-none');
          alert('Генерация не удалась: ' + (data.error || 'неизвестная ошибка'));
        }
      } catch (e) { /* сеть моргнула — пробуем дальше */ }
    }, 3000);
  }

  // --- Многофайловый режим ---
  var siteFiles = document.getElementById('site-files');
  if (siteFiles) {
    var filesCount = document.getElementById('files-count');
    siteFiles.addEventListener('change', function () {
      filesCount.textContent = siteFiles.files.length
        ? 'выбрано изображений: ' + siteFiles.files.length
        : 'изображения не выбраны';
    });
  }
})();

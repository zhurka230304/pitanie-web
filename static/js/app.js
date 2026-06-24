'use strict';

// ——— State ———
const state = {
  step: 1,
  totalSteps: 5,
  // Step 1: mode
  mode: '', // 'single' | 'full'
  // Step 2: macros
  proteins: '', fats: '', carbs: '',
  // Calculator
  calcOpen: false,
  calcSex: 'female',
  calcAge: '', calcWeight: '', calcHeight: '',
  calcActivity: 'moderate',
  calcGoal: 'maintain',
  // Step 3: meal_type (single) | meal_count (full)
  mealType: '',
  mealCount: 0,
  // Step 4: preferences per meal
  preferences: [],   // array of strings (one per meal slot)
  // Step 5: delivery
  deliveryService: 'vkusvill',
  // Results
  lastResults: null,
  lastSearchId: null,
  cartUrl: '',
  // Auth
  token: localStorage.getItem('token') || null,
  user: JSON.parse(localStorage.getItem('user') || 'null'),
};

const MEAL_TYPE_LABELS = {
  breakfast: 'Завтрак', lunch: 'Обед', dinner: 'Ужин', snack: 'Перекус',
};

const MEAL_COUNT_LABELS = {
  2: 'завтрак и обед/ужин',
  3: 'завтрак, обед, ужин',
  4: 'завтрак, обед, ужин, перекус',
  5: 'завтрак, 2 обеда, ужин, перекус',
};

const MEAL_SLOT_LABELS = {
  2: ['Завтрак', 'Обед / ужин'],
  3: ['Завтрак', 'Обед', 'Ужин'],
  4: ['Завтрак', 'Обед', 'Ужин', 'Перекус'],
  5: ['Завтрак', 'Обед', 'Ужин', 'Перекус 1', 'Перекус 2'],
};

const MEAL_SLOT_TYPE = {
  2: ['breakfast', 'lunch'],
  3: ['breakfast', 'lunch', 'dinner'],
  4: ['breakfast', 'lunch', 'dinner', 'snack'],
  5: ['breakfast', 'lunch', 'dinner', 'snack', 'snack'],
};

const DELIVERY_SERVICES = [
  { id: 'vkusvill', name: 'ВкусВилл', icon: '🟢', available: true },
  { id: 'yandex_food', name: 'Яндекс Еда', icon: '🟡', available: false },
  { id: 'yandex_lavka', name: 'Яндекс Лавка', icon: '🔵', available: false },
  { id: 'samokat', name: 'Самокат', icon: '🛵', available: false },
  { id: 'kuper', name: 'Купер', icon: '🛒', available: false },
  { id: 'ozon_fresh', name: 'Озон Фреш', icon: '🔷', available: false },
];

// Preset preferences loaded from API
let presetsData = {};

// ——— Persistence ———
function saveUserPrefs() {
  localStorage.setItem('userPrefs', JSON.stringify({
    proteins: state.proteins,
    fats: state.fats,
    carbs: state.carbs,
    calcSex: state.calcSex,
    calcAge: state.calcAge,
    calcWeight: state.calcWeight,
    calcHeight: state.calcHeight,
    calcActivity: state.calcActivity,
    calcGoal: state.calcGoal,
  }));
}

function loadUserPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem('userPrefs') || '{}');
    if (saved.proteins) state.proteins = saved.proteins;
    if (saved.fats) state.fats = saved.fats;
    if (saved.carbs) state.carbs = saved.carbs;
    if (saved.calcSex) state.calcSex = saved.calcSex;
    if (saved.calcAge) state.calcAge = saved.calcAge;
    if (saved.calcWeight) state.calcWeight = saved.calcWeight;
    if (saved.calcHeight) state.calcHeight = saved.calcHeight;
    if (saved.calcActivity) state.calcActivity = saved.calcActivity;
    if (saved.calcGoal) state.calcGoal = saved.calcGoal;
  } catch (_) {}
}

// ——— Init ———
document.addEventListener('DOMContentLoaded', async () => {
  loadUserPrefs();
  updateNavbar();
  await loadPresets();
  renderStep();
});

async function loadPresets() {
  try {
    const data = await api('/api/search/presets');
    presetsData = data.presets || {};
  } catch (_) {}
}

// ——— Pages ———
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const map = { wizard: 'pagWizard', results: 'pagResults', cabinet: 'pagCabinet', constructor: 'pagConstructor' };
  document.getElementById(map[name])?.classList.add('active');
}

// ——— Navbar ———
function updateNavbar() {
  const user = state.user;
  const navUser = document.getElementById('navUser');
  const navLogin = document.getElementById('navLogin');
  const navCabinet = document.getElementById('navCabinet');
  if (user) {
    navUser.textContent = user.name;
    navUser.style.display = 'inline-block';
    navLogin.style.display = 'none';
    navCabinet.style.display = 'inline-block';
  } else {
    navUser.style.display = 'none';
    navLogin.style.display = 'inline-block';
    navCabinet.style.display = 'none';
  }
}

// ——— Wizard ———
function resetWizard() {
  Object.assign(state, {
    step: 1, mode: '',
    calcOpen: false,
    mealType: '', mealCount: 0,
    preferences: [], deliveryService: 'vkusvill',
    lastResults: null, lastSearchId: null, cartUrl: '',
  });
  loadUserPrefs();
  renderStep();
}

function renderProgress() {
  const wrap = document.getElementById('progressDots');
  wrap.innerHTML = '';
  for (let i = 1; i <= state.totalSteps; i++) {
    const d = document.createElement('div');
    d.className = 'progress-dot' + (i < state.step ? ' done' : i === state.step ? ' active' : '');
    wrap.appendChild(d);
  }
}

function renderStep() {
  renderProgress();
  const container = document.getElementById('steps');
  const s = state.step;

  if (s === 1) container.innerHTML = renderStep1();
  else if (s === 2) container.innerHTML = renderStep2();
  else if (s === 3) container.innerHTML = renderStep3();
  else if (s === 4) container.innerHTML = renderStep4();
  else if (s === 5) container.innerHTML = renderStep5();

  attachStepEvents();
  updateCaloriesPreview();
}

function backBtn(targetStep) {
  return `<button class="step-back" onclick="goTo(${targetStep})">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
    Назад
  </button>`;
}

// Step 1: mode
function renderStep1() {
  return `
    <div class="step-title">Что подбираем?</div>
    <div class="step-subtitle">Один приём пищи или рацион на целый день?</div>
    <div class="options-grid cols-2">
      <div class="option-card ${state.mode === 'single' ? 'selected' : ''}" onclick="selectMode('single')">
        <div class="option-card-icon">🍽️</div>
        <div class="option-card-title">Один приём</div>
        <div class="option-card-desc">Подберём блюда под конкретный приём пищи</div>
      </div>
      <div class="option-card ${state.mode === 'full' ? 'selected' : ''}" onclick="selectMode('full')">
        <div class="option-card-icon">📅</div>
        <div class="option-card-title">Весь день</div>
        <div class="option-card-desc">Составим рацион на весь день по приёмам</div>
      </div>
    </div>
    <button class="btn-primary" onclick="submitStep1()" ${!state.mode ? 'disabled' : ''}>Далее →</button>
  `;
}

function onMacroInput() {
  state.proteins = document.getElementById('inP')?.value || '';
  state.fats = document.getElementById('inF')?.value || '';
  state.carbs = document.getElementById('inC')?.value || '';
  updateCaloriesPreview();
  saveUserPrefs();
}

function updateCaloriesPreview() {
  const el = document.getElementById('calPreview');
  if (!el) return;
  const p = parseFloat(state.proteins), f = parseFloat(state.fats), c = parseFloat(state.carbs);
  if (!isNaN(p) && !isNaN(f) && !isNaN(c) && (p || f || c)) {
    const kcal = Math.round(p * 4 + f * 9 + c * 4);
    el.textContent = `≈ ${kcal} ккал в день`;
  } else {
    el.textContent = 'Заполни БЖУ — покажу калории';
  }
}

function submitStep1() {
  if (!state.mode) return;
  goTo(2);
}

// Step 2: macros
function renderStep2() {
  const subtitle = state.mode === 'single'
    ? 'Укажи БЖУ конкретно для этого приёма пищи в граммах'
    : 'Укажи свою суточную норму белков, жиров и углеводов в граммах';

  const calcForm = state.calcOpen ? `
    <div class="calc-form">
      <div class="calc-row">
        <div class="calc-field">
          <div class="calc-label">Пол</div>
          <div class="calc-toggle">
            <div class="calc-toggle-btn ${state.calcSex === 'female' ? 'active' : ''}" onclick="setCalc('calcSex','female')">Женский</div>
            <div class="calc-toggle-btn ${state.calcSex === 'male' ? 'active' : ''}" onclick="setCalc('calcSex','male')">Мужской</div>
          </div>
        </div>
      </div>
      <div class="calc-row cols3">
        <div class="calc-field">
          <div class="calc-label">Возраст</div>
          <input class="calc-input" type="number" min="10" max="100" placeholder="25" value="${state.calcAge}" oninput="setCalc('calcAge', this.value)" />
        </div>
        <div class="calc-field">
          <div class="calc-label">Вес, кг</div>
          <input class="calc-input" type="number" min="30" max="300" placeholder="60" value="${state.calcWeight}" oninput="setCalc('calcWeight', this.value)" />
        </div>
        <div class="calc-field">
          <div class="calc-label">Рост, см</div>
          <input class="calc-input" type="number" min="100" max="250" placeholder="165" value="${state.calcHeight}" oninput="setCalc('calcHeight', this.value)" />
        </div>
      </div>
      <div class="calc-field" style="margin-bottom:10px">
        <div class="calc-label">Активность</div>
        <select class="calc-select" onchange="setCalc('calcActivity', this.value)">
          <option value="sedentary" ${state.calcActivity === 'sedentary' ? 'selected' : ''}>Минимальная (сидячая работа)</option>
          <option value="light" ${state.calcActivity === 'light' ? 'selected' : ''}>Низкая (1–3 тренировки в неделю)</option>
          <option value="moderate" ${state.calcActivity === 'moderate' ? 'selected' : ''}>Средняя (3–5 тренировок в неделю)</option>
          <option value="active" ${state.calcActivity === 'active' ? 'selected' : ''}>Высокая (6–7 тренировок в неделю)</option>
          <option value="very_active" ${state.calcActivity === 'very_active' ? 'selected' : ''}>Очень высокая (2 тренировки в день)</option>
        </select>
      </div>
      <div class="calc-field" style="margin-bottom:14px">
        <div class="calc-label">Цель</div>
        <div class="calc-toggle">
          <div class="calc-toggle-btn ${state.calcGoal === 'lose' ? 'active' : ''}" onclick="setCalc('calcGoal','lose')">Похудеть</div>
          <div class="calc-toggle-btn ${state.calcGoal === 'maintain' ? 'active' : ''}" onclick="setCalc('calcGoal','maintain')">Держать вес</div>
          <div class="calc-toggle-btn ${state.calcGoal === 'gain' ? 'active' : ''}" onclick="setCalc('calcGoal','gain')">Набрать</div>
        </div>
      </div>
      <div class="error-msg" id="calcError"></div>
      <button class="btn-secondary" onclick="calcMacros()">Рассчитать и заполнить</button>
    </div>
  ` : '';

  return `
    ${backBtn(1)}
    <div class="step-title">Введи БЖУ</div>
    <div class="step-subtitle">${subtitle}</div>
    <div style="text-align:right;margin-bottom:12px">
      <span class="nav-link" style="font-size:0.82rem" onclick="toggleCalc()">${state.calcOpen ? 'Скрыть калькулятор ↑' : 'Не знаю свои цифры — рассчитать ✦'}</span>
    </div>
    ${calcForm}
    <div class="macro-grid">
      <div class="macro-field">
        <div class="macro-label">Белки</div>
        <input class="macro-input" id="inP" type="number" min="0" max="500" placeholder="120" value="${state.proteins}" oninput="onMacroInput()" />
        <div class="macro-unit">г</div>
      </div>
      <div class="macro-field">
        <div class="macro-label">Жиры</div>
        <input class="macro-input" id="inF" type="number" min="0" max="500" placeholder="50" value="${state.fats}" oninput="onMacroInput()" />
        <div class="macro-unit">г</div>
      </div>
      <div class="macro-field">
        <div class="macro-label">Углеводы</div>
        <input class="macro-input" id="inC" type="number" min="0" max="800" placeholder="180" value="${state.carbs}" oninput="onMacroInput()" />
        <div class="macro-unit">г</div>
      </div>
    </div>
    <div class="calories-preview" id="calPreview">Заполни БЖУ — покажу калории</div>
    <div class="error-msg" id="macroError"></div>
    <button class="btn-primary" onclick="submitStep2()">Далее →</button>
  `;
}

function toggleCalc() {
  state.calcOpen = !state.calcOpen;
  renderStep();
}

function setCalc(field, value) {
  state[field] = value;
  saveUserPrefs();
  if (['calcSex', 'calcActivity', 'calcGoal'].includes(field)) renderStep();
}

function calcMacros() {
  const age = parseFloat(state.calcAge);
  const weight = parseFloat(state.calcWeight);
  const height = parseFloat(state.calcHeight);
  const err = document.getElementById('calcError');

  if (isNaN(age) || isNaN(weight) || isNaN(height)) {
    showError(err, 'Заполни возраст, вес и рост'); return;
  }
  if (age < 10 || age > 100) { showError(err, 'Возраст: 10–100 лет'); return; }
  if (weight < 30 || weight > 300) { showError(err, 'Вес: 30–300 кг'); return; }
  if (height < 100 || height > 250) { showError(err, 'Рост: 100–250 см'); return; }
  hideError(err);

  // Mifflin-St Jeor
  const bmr = state.calcSex === 'male'
    ? 10 * weight + 6.25 * height - 5 * age + 5
    : 10 * weight + 6.25 * height - 5 * age - 161;

  const activityMap = { sedentary: 1.2, light: 1.375, moderate: 1.55, active: 1.725, very_active: 1.9 };
  const goalMap = { lose: 0.8, maintain: 1.0, gain: 1.15 };

  const kcal = Math.round(bmr * activityMap[state.calcActivity] * goalMap[state.calcGoal]);

  // Split: protein 2g/kg, fat 25% kcal, carbs — rest
  const protein = Math.round(weight * 2);
  const fat = Math.round(kcal * 0.25 / 9);
  const carbs = Math.round((kcal - protein * 4 - fat * 9) / 4);

  state.proteins = String(protein);
  state.fats = String(fat);
  state.carbs = String(Math.max(carbs, 0));
  state.calcOpen = false;
  saveUserPrefs();
  renderStep();
  showToast('БЖУ рассчитано и заполнено!', 'success');
}

function selectMode(mode) {
  state.mode = mode;
  renderStep();
}

function submitStep2() {
  const p = parseFloat(state.proteins), f = parseFloat(state.fats), c = parseFloat(state.carbs);
  const err = document.getElementById('macroError');
  if (isNaN(p) || isNaN(f) || isNaN(c)) { showError(err, 'Заполни все три поля'); return; }
  if (p < 0 || p > 500) { showError(err, 'Белки: 0–500 г'); return; }
  if (f < 0 || f > 500) { showError(err, 'Жиры: 0–500 г'); return; }
  if (c < 0 || c > 800) { showError(err, 'Углеводы: 0–800 г'); return; }
  if (p + f + c < 10) { showError(err, 'Слишком маленькие значения'); return; }
  hideError(err);
  goTo(3);
}

// Step 3: meal type or meal count
function renderStep3() {
  if (state.mode === 'single') {
    const types = [
      { id: 'breakfast', icon: '☀️', label: 'Завтрак', desc: 'Утренний приём пищи' },
      { id: 'lunch', icon: '🥘', label: 'Обед', desc: 'Основной дневной приём' },
      { id: 'dinner', icon: '🌙', label: 'Ужин', desc: 'Вечерний приём пищи' },
      { id: 'snack', icon: '🍎', label: 'Перекус', desc: 'Лёгкий промежуточный приём' },
    ];
    return `
      ${backBtn(2)}
      <div class="step-title">Какой приём пищи?</div>
      <div class="step-subtitle">Выбери, для какого приёма подбираем блюда</div>
      <div class="options-grid cols-2">
        ${types.map(t => `
          <div class="option-card ${state.mealType === t.id ? 'selected' : ''}" onclick="selectMealType('${t.id}')">
            <div class="option-card-icon">${t.icon}</div>
            <div class="option-card-title">${t.label}</div>
            <div class="option-card-desc">${t.desc}</div>
          </div>
        `).join('')}
      </div>
      <button class="btn-primary" onclick="submitStep3()" ${!state.mealType ? 'disabled' : ''}>Далее →</button>
    `;
  } else {
    const counts = [
      { n: 2, desc: 'Завтрак + обед' },
      { n: 3, desc: 'Завтрак + обед + ужин' },
      { n: 4, desc: '+ перекус' },
      { n: 5, desc: '+ 2 перекуса' },
    ];
    return `
      ${backBtn(2)}
      <div class="step-title">Сколько приёмов пищи?</div>
      <div class="step-subtitle">Выбери количество приёмов для дневного рациона</div>
      <div class="options-grid cols-4">
        ${counts.map(c => `
          <div class="option-card compact ${state.mealCount === c.n ? 'selected' : ''}" onclick="selectMealCount(${c.n})">
            <div class="option-card-title">${c.n}</div>
            <div class="option-card-desc">${c.desc}</div>
          </div>
        `).join('')}
      </div>
      <button class="btn-primary" onclick="submitStep3()" ${!state.mealCount ? 'disabled' : ''}>Далее →</button>
    `;
  }
}

function selectMealType(t) { state.mealType = t; renderStep(); }
function selectMealCount(n) { state.mealCount = n; renderStep(); }

function submitStep3() {
  if (state.mode === 'single' && !state.mealType) return;
  if (state.mode === 'full' && !state.mealCount) return;
  // Init preferences array
  const slots = state.mode === 'single' ? 1 : state.mealCount;
  if (state.preferences.length !== slots) state.preferences = Array(slots).fill('');
  goTo(4);
}

// Step 4: preferences
function renderStep4() {
  const slots = state.mode === 'single'
    ? [{ label: MEAL_TYPE_LABELS[state.mealType] || 'Приём', type: state.mealType }]
    : (MEAL_SLOT_LABELS[state.mealCount] || []).map((label, i) => ({
        label, type: MEAL_SLOT_TYPE[state.mealCount]?.[i] || 'lunch',
      }));

  const blocks = slots.map((slot, i) => {
    const chips = (presetsData[slot.type] || []).map(p => `
      <div class="preset-chip ${state.preferences[i] === p.value ? 'selected' : ''}"
           onclick="selectPreset(${i}, '${p.value}')">
        ${p.label}
      </div>
    `).join('');

    return `
      <div class="meal-pref-block">
        <div class="meal-pref-label">${slot.label}</div>
        <div class="presets-chips" style="margin-bottom:10px">${chips}</div>
        <input class="text-input" type="text"
          placeholder="Или напиши своё пожелание..."
          value="${state.preferences[i] || ''}"
          oninput="onPrefInput(${i}, this.value)"
          id="prefInput${i}"
        />
      </div>
    `;
  }).join('');

  return `
    ${backBtn(3)}
    <div class="step-title">Пожелания к блюдам</div>
    <div class="step-subtitle">Выбери или напиши, что хочешь — или пропусти</div>
    ${blocks}
    <button class="btn-primary" onclick="submitStep4()">Далее →</button>
    <button class="btn-secondary" style="margin-top:10px" onclick="skipPrefs()">Пропустить</button>
  `;
}

function selectPreset(index, value) {
  // Toggle: click same chip to deselect
  state.preferences[index] = state.preferences[index] === value ? '' : value;
  const input = document.getElementById(`prefInput${index}`);
  if (input) input.value = state.preferences[index];
  renderStep();
}

function onPrefInput(index, value) {
  state.preferences[index] = value;
  // Deselect chips if user typed manually
  renderProgress(); // just refresh dots, not full re-render
}

function submitStep4() {
  goTo(5);
}
function skipPrefs() {
  const slots = state.mode === 'single' ? 1 : state.mealCount;
  state.preferences = Array(slots).fill('');
  goTo(5);
}

// Step 5: delivery service
function renderStep5() {
  const cards = DELIVERY_SERVICES.map(s => `
    <div class="service-card ${!s.available ? 'soon' : ''} ${state.deliveryService === s.id ? 'selected' : ''}"
         onclick="${s.available ? `selectService('${s.id}')` : ''}">
      ${!s.available ? '<span class="service-soon-badge">Скоро</span>' : ''}
      <div class="service-icon">${s.icon}</div>
      <div class="service-name">${s.name}</div>
    </div>
  `).join('');

  const p = parseFloat(state.proteins), f = parseFloat(state.fats), c = parseFloat(state.carbs);
  const kcal = Math.round(p * 4 + f * 9 + c * 4);

  return `
    ${backBtn(4)}
    <div class="step-title">Откуда заказываем?</div>
    <div class="step-subtitle">Выбери сервис доставки. Сейчас доступен ВкусВилл, остальные скоро.</div>
    <div class="services-grid">${cards}</div>
    <div style="margin-top:20px;padding:14px 16px;background:var(--green-light);border-radius:var(--radius-sm);font-size:0.875rem;color:var(--green-dark);">
      <strong>Итого:</strong> Б ${state.proteins}г · Ж ${state.fats}г · У ${state.carbs}г · ${kcal} ккал
      ${state.mode === 'single'
        ? ' · ' + (MEAL_TYPE_LABELS[state.mealType] || '') + (state.preferences[0] ? ' · ' + state.preferences[0] : '')
        : ' · ' + state.mealCount + ' приёма' + (state.preferences.some(p => p) ? ' · ' + state.preferences.filter(p => p).join(', ') : '')
      }
    </div>
    <button class="btn-primary" onclick="startSearch()">Найти блюда 🔍</button>
  `;
}

function selectService(id) {
  state.deliveryService = id;
  renderStep();
}

// ——— Navigation ———
function goTo(step) {
  state.step = step;
  renderStep();
}

function attachStepEvents() {
  // auto-focus first input in step 2 (macros)
  if (state.step === 2) {
    setTimeout(() => document.getElementById('inP')?.focus(), 50);
  }
}

// ——— Search ———
async function startSearch() {
  const isFull = state.mode === 'full';
  showLoadingAnimated(isFull ? [
    'Подбираем завтрак...',
    'Ищем блюда для обеда...',
    'Составляем ужин...',
    'Подбираем перекусы...',
    'Почти готово, ещё немного...',
  ] : [
    'Ищем блюда в ВкусВилле...',
    'Анализируем состав и КБЖУ...',
    'Выбираем лучшее для тебя...',
  ], 3500, isFull ? 40 : 20);

  try {
    let result;
    const P = parseFloat(state.proteins), F = parseFloat(state.fats), C = parseFloat(state.carbs);

    if (state.mode === 'single') {
      result = await api('/api/search/single', 'POST', {
        proteins: P, fats: F, carbs: C,
        meal_type: state.mealType,
        preference: state.preferences[0] || null,
        delivery_service: state.deliveryService,
      });
    } else {
      result = await api('/api/search/full-day', 'POST', {
        proteins: P, fats: F, carbs: C,
        meal_count: state.mealCount,
        preferences: state.preferences.map(p => p || null),
        delivery_service: state.deliveryService,
      });
    }

    state.lastResults = result;
    state.lastSearchId = result.search_id || null;
    state.cartUrl = result.cart_url || '';
    saveUserPrefs();

    renderResults(result);
    showPage('results');
  } catch (e) {
    showToast(e.message || 'Ошибка поиска. Попробуй ещё раз.', 'error');
  } finally {
    hideLoading();
  }
}

// ——— Results rendering ———
function renderResults(data) {
  const titleEl = document.getElementById('resultsTitle');
  const metaEl = document.getElementById('resultsMeta');
  const cartBtn = document.getElementById('btnCart');
  const content = document.getElementById('resultsContent');

  const kcal = Math.round(
    parseFloat(state.proteins) * 4 + parseFloat(state.fats) * 9 + parseFloat(state.carbs) * 4
  );
  metaEl.textContent = `Б ${state.proteins}г · Ж ${state.fats}г · У ${state.carbs}г · ${kcal} ккал`;

  if (state.cartUrl) {
    cartBtn.style.display = 'inline-block';
  } else {
    cartBtn.style.display = 'none';
  }

  if (data.combinations) {
    // Single mode — 3 combinations
    titleEl.textContent = `${MEAL_TYPE_LABELS[state.mealType] || 'Приём'} — варианты для тебя`;
    cartBtn.style.display = 'none';
    content.innerHTML = data.combinations.map((combo, i) => renderCombination(combo, i)).join('');
  } else if (data.items) {
    // Legacy single mode
    titleEl.textContent = `${MEAL_TYPE_LABELS[state.mealType] || 'Приём'} — блюда для тебя`;
    content.innerHTML = `<div class="cards-grid">${data.items.map(renderDishCard).join('')}</div>`;
  } else if (data.meals) {
    // Full day mode
    titleEl.textContent = 'Рацион на день';
    content.innerHTML = data.meals.map(meal => `
      <div class="meal-section">
        <div class="meal-section-label">${meal.label} — цель ${meal.target_calories} ккал</div>
        <div class="cards-grid">${meal.items.map(renderDishCard).join('')}</div>
      </div>
    `).join('');
  }
}

function renderDishCard(item) {
  const imgHtml = item.image_url
    ? `<img class="dish-card-img" src="${item.image_url}" alt="${escHtml(item.name)}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" /><div class="dish-card-img-placeholder" style="display:none">🥗</div>`
    : `<div class="dish-card-img-placeholder">🥗</div>`;

  let pillsHtml = '';
  if (item.nutrition) {
    const n = item.nutrition;
    pillsHtml = `
      <div class="dish-kbju">
        <span class="kbju-pill">Б ${n.protein}г</span>
        <span class="kbju-pill">Ж ${n.fat}г</span>
        <span class="kbju-pill">У ${n.carbohydrates}г</span>
        <span class="kbju-pill kcal">${n.calories} ккал</span>
      </div>
    `;
  }

  const portionText = item.portion_hint
    ? `${item.needed_g} г (${item.portion_hint})`
    : item.needed_g ? `${item.needed_g} г` : '';

  let per100Html = '';
  if (item.nutrition?.per_100g) {
    const p = item.nutrition.per_100g;
    per100Html = `<div style="margin-top:6px;font-size:0.78rem;opacity:0.8">На 100г: Б${p.protein} Ж${p.fat} У${p.carbohydrates} К${p.calories}</div>`;
  }

  const priceHtml = item.price ? `<div style="font-size:0.8rem;color:var(--green-dark);font-weight:600;margin-top:4px">${item.price} ₽</div>` : '';

  return `
    <div class="dish-card" id="card-${item.id}" onclick="toggleCard(${item.id})">
      ${imgHtml}
      <div class="dish-card-body">
        <div class="dish-card-name">${escHtml(item.name)}</div>
        ${pillsHtml}
        ${portionText ? `<div class="dish-portion">Нужно: ${portionText}</div>` : ''}
        ${priceHtml}
      </div>
      <div class="dish-card-expand">
        ${per100Html}
        <a class="dish-expand-link" href="${item.url}" target="_blank" rel="noopener">Открыть в ВкусВилле →</a>
        <button class="dish-select-btn" id="selectBtn-${item.id}" onclick="event.stopPropagation();toggleSelectDish(${item.id}, '${escHtml(item.name)}', ${item.needed_g})">
          Добавить в заказ
        </button>
      </div>
    </div>
  `;
}

function renderCombination(combo, index) {
  const t = combo.total || {};
  const totalHtml = `
    <div class="combo-total">
      <span class="kbju-pill">Б ${t.protein ?? '—'}г</span>
      <span class="kbju-pill">Ж ${t.fat ?? '—'}г</span>
      <span class="kbju-pill">У ${t.carbohydrates ?? '—'}г</span>
      <span class="kbju-pill kcal">${t.calories ?? '—'} ккал</span>
    </div>`;

  const dishesHtml = combo.items.map(item => {
    const img = item.image_url
      ? `<img class="combo-dish-img" src="${item.image_url}" alt="" loading="lazy" onerror="this.style.display='none'">`
      : `<div class="combo-dish-img-placeholder"></div>`;
    const portion = item.portion_hint
      ? `${item.needed_g} г (${item.portion_hint})`
      : item.needed_g ? `${item.needed_g} г` : '';
    const price = item.price ? `<span class="combo-dish-price">${item.price} ₽</span>` : '';
    const mealType = state.mealType || '';
    return `
      <div class="combo-dish">
        ${img}
        <div class="combo-dish-info">
          <a class="combo-dish-name" href="${item.url}" target="_blank" rel="noopener">${escHtml(item.name)}</a>
          <div class="combo-dish-meta">${portion ? `Нужно: ${portion}` : ''} ${price}</div>
        </div>
        <div class="dish-rating-btns">
          <button class="dish-rate-btn like" title="Нравится" onclick="event.stopPropagation();rateDish('${item.xml_id}','${mealType}','${escHtml(item.name).replace(/'/g,"\\'")}',1,this)">❤️</button>
          <button class="dish-rate-btn dislike" title="Не показывать" onclick="event.stopPropagation();rateDish('${item.xml_id}','${mealType}','${escHtml(item.name).replace(/'/g,"\\'")}', -1,this)">✕</button>
        </div>
      </div>`;
  }).join('');

  const cartHtml = combo.cart_url
    ? `<a class="btn combo-cart-btn" href="${combo.cart_url}" target="_blank" rel="noopener">Открыть в ВкусВилле →</a>`
    : '';

  const xmlIds = combo.items.map(i => String(i.xml_id));
  const searchId = state.lastSearchId || 0;
  const mealType = state.mealType || '';
  const comboRatingHtml = state.user ? `
    <div class="combo-rating-btns">
      <button class="combo-rate-btn" onclick="rateCombo(${searchId},'${mealType}',${index},[${xmlIds.map(x=>`'${x}'`).join(',')}],1,this)">❤️ Понравился</button>
      <button class="combo-rate-btn" onclick="rateCombo(${searchId},'${mealType}',${index},[${xmlIds.map(x=>`'${x}'`).join(',')}],-1,this)">✕ Не то</button>
    </div>` : '';

  return `
    <div class="combo-card">
      <div class="combo-header">
        <span class="combo-num">Вариант ${index + 1}</span>
        ${totalHtml}
      </div>
      <div class="combo-dishes">${dishesHtml}</div>
      ${comboRatingHtml}
      ${cartHtml}
    </div>`;
}

function toggleCard(id) {
  const card = document.getElementById(`card-${id}`);
  card?.classList.toggle('expanded');
}

// ——— Order tracking ———
const selectedDishes = new Map(); // id -> {name, portion_g}

function toggleSelectDish(id, name, needed_g) {
  const btn = document.getElementById(`selectBtn-${id}`);
  if (selectedDishes.has(id)) {
    selectedDishes.delete(id);
    btn?.classList.remove('checked');
    btn && (btn.textContent = 'Добавить в заказ');
  } else {
    selectedDishes.set(id, { name, portion_g: needed_g });
    btn?.classList.add('checked');
    btn && (btn.textContent = '✓ Добавлено');
  }
}

function openCart() {
  if (state.cartUrl) window.open(state.cartUrl, '_blank');
}

function openOrderModal() {
  if (!state.user) {
    showToast('Войди в аккаунт, чтобы сохранить заказ');
    setTimeout(openAuthModal, 600);
    return;
  }
  if (!state.lastSearchId) {
    showToast('Войди в аккаунт перед поиском — тогда заказ сохранится', 'error');
    return;
  }

  // Populate all dishes from results
  const orderList = document.getElementById('orderedItemsList');
  const allItems = getAllResultItems();
  orderList.innerHTML = allItems.map(item => `
    <div class="ordered-item">
      <input type="checkbox" id="oi-${item.id}" ${selectedDishes.has(item.id) ? 'checked' : ''} />
      <label class="ordered-item-name" for="oi-${item.id}">${escHtml(item.name)}</label>
      <span style="font-size:0.78rem;color:var(--text-muted)">${item.needed_g ? item.needed_g + 'г' : ''}</span>
    </div>
  `).join('');

  document.getElementById('orderNotes').value = '';
  document.getElementById('orderError').style.display = 'none';
  document.getElementById('orderModal').classList.add('visible');
}

function getAllResultItems() {
  const data = state.lastResults;
  if (!data) return [];
  if (data.items) return data.items;
  if (data.meals) return data.meals.flatMap(m => m.items);
  return [];
}

function closeOrderModal() {
  document.getElementById('orderModal').classList.remove('visible');
}

async function saveOrder() {
  const allItems = getAllResultItems();
  const checkedItems = allItems.filter(item => {
    const cb = document.getElementById(`oi-${item.id}`);
    return cb?.checked;
  });

  if (checkedItems.length === 0) {
    showError(document.getElementById('orderError'), 'Отметь хотя бы одно блюдо');
    return;
  }

  const ordered = checkedItems.map(item => ({
    name: item.name, url: item.url, portion_g: item.needed_g || 0,
  }));
  const notes = document.getElementById('orderNotes').value.trim();

  try {
    await api('/api/history/order', 'POST', {
      search_id: state.lastSearchId,
      ordered_items: ordered,
      delivery_service: state.deliveryService,
      notes: notes || null,
    });
    closeOrderModal();
    showToast('Заказ сохранён!', 'success');
  } catch (e) {
    if (e.message?.includes('401') || e.message?.includes('авторизован')) {
      closeOrderModal();
      showToast('Войди в аккаунт для сохранения');
      setTimeout(openAuthModal, 600);
    } else {
      showError(document.getElementById('orderError'), e.message || 'Ошибка сохранения');
    }
  }
}

// ——— Cabinet ———
let cabinetTab = 'searches';

async function showCabinet() {
  if (!state.user) { openAuthModal(); return; }
  showPage('cabinet');
  document.getElementById('cabinetTitle').textContent = `Привет, ${state.user.name}!`;
  document.getElementById('cabinetSub').textContent = `${state.user.email}`;
  await loadCabinetTab('searches');
}

function switchCabinetTab(tab) {
  cabinetTab = tab;
  document.getElementById('tabSearches').className = 'btn-sm ' + (tab === 'searches' ? 'outline' : 'gray');
  document.getElementById('tabOrders').className = 'btn-sm ' + (tab === 'orders' ? 'outline' : 'gray');
  document.getElementById('tabFavorites').className = 'btn-sm ' + (tab === 'favorites' ? 'outline' : 'gray');
  loadCabinetTab(tab);
}

async function loadCabinetTab(tab) {
  const content = document.getElementById('cabinetContent');
  content.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted)">Загружаем...</div>';
  try {
    if (tab === 'searches') {
      const data = await api('/api/history/');
      renderSearchHistory(data.items || []);
    } else if (tab === 'favorites') {
      await renderFavorites();
    } else {
      const data = await api('/api/history/orders/all');
      renderOrderHistory(data.items || []);
    }
  } catch (e) {
    content.innerHTML = `<div class="empty-state"><div class="empty-state-icon">⚠️</div><div class="empty-state-title">Не удалось загрузить</div></div>`;
  }
}

function renderSearchHistory(items) {
  const content = document.getElementById('cabinetContent');
  if (!items.length) {
    content.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">🔍</div>
        <div class="empty-state-title">Пока нет поисков</div>
        <div class="empty-state-desc">Подбери блюда — и они появятся здесь</div>
      </div>`;
    return;
  }

  const MODE_LABELS = { single: 'Один приём', full: 'Весь день' };
  const html = items.map(item => {
    const date = new Date(item.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
    return `
      <div class="history-card" onclick="loadHistoryDetail(${item.id})">
        <div class="history-card-icon">${item.mode === 'full' ? '📅' : '🍽️'}</div>
        <div class="history-card-info">
          <div class="history-card-date">${date}</div>
          <div class="history-card-title">${MODE_LABELS[item.mode] || item.mode}${item.meal_type ? ' — ' + (MEAL_TYPE_LABELS[item.meal_type] || item.meal_type) : ''}</div>
          <div class="history-card-meta">Б${item.proteins}г · Ж${item.fats}г · У${item.carbs}г · ${Math.round(item.calories)} ккал</div>
        </div>
        <span class="history-card-badge ${item.has_order ? 'badge-green' : 'badge-gray'}">${item.has_order ? 'Заказано' : 'Без заказа'}</span>
      </div>
    `;
  }).join('');
  content.innerHTML = `<div class="history-list">${html}</div>`;
}

async function loadHistoryDetail(searchId) {
  showLoading('Загружаем...');
  try {
    const data = await api(`/api/history/${searchId}`);
    state.lastResults = data;
    state.lastSearchId = searchId;
    state.cartUrl = data.cart_url || '';
    state.proteins = String(data.proteins || '');
    state.fats = String(data.fats || '');
    state.carbs = String(data.carbs || '');
    state.mode = data.meal_count ? 'full' : 'single';
    state.mealType = data.meal_type || '';
    state.mealCount = data.meal_count || 0;
    state.deliveryService = data.delivery_service || 'vkusvill';
    renderResults(data);
    showPage('results');
  } catch (e) {
    showToast('Не удалось загрузить', 'error');
  } finally {
    hideLoading();
  }
}

function renderOrderHistory(items) {
  const content = document.getElementById('cabinetContent');
  if (!items.length) {
    content.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">🛒</div>
        <div class="empty-state-title">Заказов пока нет</div>
        <div class="empty-state-desc">После поиска блюд нажми «Что заказал?» — и всё сохранится здесь</div>
      </div>`;
    return;
  }
  const html = items.map(item => {
    const date = new Date(item.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
    const names = item.ordered_items.map(i => i.name).join(', ');
    return `
      <div class="history-card">
        <div class="history-card-icon">🛒</div>
        <div class="history-card-info">
          <div class="history-card-date">${date}</div>
          <div class="history-card-title">${escHtml(names)}</div>
          ${item.notes ? `<div class="history-card-meta">${escHtml(item.notes)}</div>` : ''}
        </div>
        <span class="history-card-badge badge-green">${item.delivery_service}</span>
      </div>
    `;
  }).join('');
  content.innerHTML = `<div class="history-list">${html}</div>`;
}

// ——— Auth ———
function openAuthModal() {
  document.getElementById('authModal').classList.add('visible');
  switchAuthTab('login');
}
function closeAuthModal() {
  document.getElementById('authModal').classList.remove('visible');
}

function switchAuthTab(tab) {
  document.getElementById('formLogin').style.display = tab === 'login' ? 'block' : 'none';
  document.getElementById('formRegister').style.display = tab === 'register' ? 'block' : 'none';
  document.getElementById('tabLogin').className = 'auth-tab' + (tab === 'login' ? ' active' : '');
  document.getElementById('tabRegister').className = 'auth-tab' + (tab === 'register' ? ' active' : '');
}

async function doLogin() {
  const email = document.getElementById('loginEmail').value.trim();
  const password = document.getElementById('loginPassword').value;
  const err = document.getElementById('loginError');
  if (!email || !password) { showError(err, 'Заполни все поля'); return; }
  try {
    const data = await api('/api/auth/login', 'POST', { email, password });
    onAuthSuccess(data);
  } catch (e) {
    showError(err, e.message || 'Ошибка входа');
  }
}

async function doRegister() {
  const name = document.getElementById('regName').value.trim();
  const email = document.getElementById('regEmail').value.trim();
  const password = document.getElementById('regPassword').value;
  const err = document.getElementById('regError');
  if (!name || !email || !password) { showError(err, 'Заполни все поля'); return; }
  try {
    const data = await api('/api/auth/register', 'POST', { name, email, password });
    onAuthSuccess(data);
  } catch (e) {
    showError(err, e.message || 'Ошибка регистрации');
  }
}

function onAuthSuccess(data) {
  state.token = data.token;
  state.user = data.user;
  localStorage.setItem('token', data.token);
  localStorage.setItem('user', JSON.stringify(data.user));
  closeAuthModal();
  updateNavbar();
  showToast(`Привет, ${data.user.name}! 👋`, 'success');
}

function logout() {
  state.token = null;
  state.user = null;
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  updateNavbar();
  showPage('wizard');
  resetWizard();
  showToast('До свидания!');
}

// ——— API helper ———
async function api(url, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (state.token) opts.headers['Authorization'] = `Bearer ${state.token}`;
  if (body) opts.body = JSON.stringify(body);

  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    const msg = data.detail || data.message || `Ошибка ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// ——— Ratings ———
async function rateDish(xml_id, meal_type, name, rating, btn) {
  if (!state.user) { showToast('Войди в аккаунт для оценки'); return; }
  const isActive = btn.classList.contains('active');
  const finalRating = isActive ? 0 : rating;
  try {
    await api('/api/ratings/dish', 'POST', { dish_xml_id: String(xml_id), dish_name: name, meal_type, rating: finalRating });
    // update sibling buttons
    const wrap = btn.closest('.dish-rating-btns');
    wrap.querySelectorAll('button').forEach(b => b.classList.remove('active'));
    if (finalRating !== 0) btn.classList.add('active');
  } catch (e) { showToast('Не удалось сохранить оценку', 'error'); }
}

async function rateCombo(searchId, mealType, comboIndex, xmlIds, rating, btn) {
  if (!state.user) { showToast('Войди в аккаунт для оценки'); return; }
  const isActive = btn.classList.contains('active');
  if (isActive) return;
  try {
    await api('/api/ratings/combo', 'POST', { search_id: searchId, meal_type: mealType, combo_index: comboIndex, dish_xml_ids: xmlIds, rating });
    btn.closest('.combo-rating-btns').querySelectorAll('button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  } catch (e) { showToast('Не удалось сохранить оценку', 'error'); }
}

async function renderFavorites() {
  const content = document.getElementById('cabinetContent');
  const MEAL_TABS = ['breakfast', 'lunch', 'dinner', 'snack'];
  let activeMealTab = 'breakfast';

  async function renderFavContent() {
    const data = await api(`/api/ratings/dishes?meal_type=${activeMealTab}`);
    const liked = data.liked || [];
    const disliked = data.disliked || [];

    const likedHtml = liked.length
      ? liked.map(d => `
        <div class="fav-item">
          <span class="fav-item-name">${escHtml(d.name)}</span>
          <button class="fav-remove-btn" onclick="removeDishRating('${d.xml_id}','${activeMealTab}',this)">✕</button>
        </div>`).join('')
      : '<div style="color:var(--text-muted);font-size:0.9rem">Нет понравившихся блюд</div>';

    const dislikedHtml = disliked.length
      ? disliked.map(d => `
        <div class="fav-item">
          <span class="fav-item-name">${escHtml(d.name)}</span>
          <button class="fav-restore-btn" onclick="removeDishRating('${d.xml_id}','${activeMealTab}',this)">↩</button>
        </div>`).join('')
      : '<div style="color:var(--text-muted);font-size:0.9rem">Нет скрытых блюд</div>';

    document.getElementById('favContent').innerHTML = `
      <div class="fav-section"><div class="fav-section-title">❤️ Понравившиеся</div>${likedHtml}</div>
      <div class="fav-section"><div class="fav-section-title">✕ Скрытые</div>${dislikedHtml}</div>
    `;
  }

  content.innerHTML = `
    <div class="fav-meal-tabs">
      ${MEAL_TABS.map(t => `<button class="btn-sm ${t === activeMealTab ? 'outline' : 'gray'}" id="favTab-${t}" onclick="switchFavTab('${t}')">${MEAL_TYPE_LABELS[t]}</button>`).join('')}
    </div>
    <div id="favContent"></div>
  `;

  window.switchFavTab = async (tab) => {
    activeMealTab = tab;
    MEAL_TABS.forEach(t => {
      document.getElementById(`favTab-${t}`).className = 'btn-sm ' + (t === tab ? 'outline' : 'gray');
    });
    await renderFavContent();
  };

  await renderFavContent();
}

async function removeDishRating(xml_id, meal_type, btn) {
  try {
    const name = btn.closest('.fav-item').querySelector('.fav-item-name').textContent;
    await api('/api/ratings/dish', 'POST', { dish_xml_id: xml_id, dish_name: name, meal_type, rating: 0 });
    btn.closest('.fav-item').remove();
  } catch (e) { showToast('Ошибка', 'error'); }
}

// ——— UI helpers ———
let _loadingTimer = null;
let _countdownTimer = null;

function startCountdown(seconds) {
  const el = document.getElementById('loadingCountdown');
  let remaining = seconds;
  el.textContent = `примерно ${remaining} с`;
  _countdownTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      el.textContent = 'почти готово...';
      clearInterval(_countdownTimer);
      _countdownTimer = null;
    } else {
      el.textContent = `примерно ${remaining} с`;
    }
  }, 1000);
}

function stopCountdown() {
  const el = document.getElementById('loadingCountdown');
  if (el) el.textContent = '';
  if (_countdownTimer) { clearInterval(_countdownTimer); _countdownTimer = null; }
}

function showLoading(text = 'Загружаем...') {
  document.getElementById('loadingText').textContent = text;
  document.getElementById('loadingOverlay').classList.add('visible');
}

function showLoadingAnimated(messages, interval = 3500, estimatedSeconds = 20) {
  let i = 0;
  const el = document.getElementById('loadingText');
  el.textContent = messages[0];
  document.getElementById('loadingOverlay').classList.add('visible');
  _loadingTimer = setInterval(() => {
    i = (i + 1) % messages.length;
    el.textContent = messages[i];
  }, interval);
  startCountdown(estimatedSeconds);
}

function hideLoading() {
  document.getElementById('loadingOverlay').classList.remove('visible');
  if (_loadingTimer) { clearInterval(_loadingTimer); _loadingTimer = null; }
  stopCountdown();
}

function showError(el, msg) {
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
}
function hideError(el) {
  if (!el) return;
  el.style.display = 'none';
}

let toastTimer;
function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (type ? ' ' + type : '');
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3000);
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Close modals on overlay click
document.getElementById('authModal').addEventListener('click', e => {
  if (e.target === document.getElementById('authModal')) closeAuthModal();
});
document.getElementById('orderModal').addEventListener('click', e => {
  if (e.target === document.getElementById('orderModal')) closeOrderModal();
});

// Enter key for auth forms
document.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    if (document.getElementById('authModal').classList.contains('visible')) {
      if (document.getElementById('formLogin').style.display !== 'none') doLogin();
      else doRegister();
    }
  }
});

// ——— Constructor ———

const CONSTR_CATS = [
  { id: 'all',        label: 'Все' },
  { id: 'meat',       label: 'Мясо и птица' },
  { id: 'fish',       label: 'Рыба' },
  { id: 'dairy',      label: 'Яйца и молочные' },
  { id: 'vegetables', label: 'Овощи' },
  { id: 'greens',     label: 'Зелень' },
  { id: 'grains',     label: 'Крупы и бобовые' },
  { id: 'nuts',       label: 'Орехи и семена' },
  { id: 'fruits',     label: 'Фрукты' },
  { id: 'oils',       label: 'Масла и соусы' },
];

let constrIngredients = [];
let constrSelected = {};
let constrActiveCategory = 'all';
async function showConstructor() {
  showPage('constructor');
  if (!constrIngredients.length) {
    try {
      const r = await fetch('/static/data/ingredients.json');
      constrIngredients = await r.json();
    } catch (_) {
      constrIngredients = [];
    }
  }
  renderConstrCats();
  renderConstrItems();
}

function renderConstrCats() {
  document.getElementById('constrCats').innerHTML = CONSTR_CATS.map(c =>
    `<button class="constr-cat-btn${constrActiveCategory === c.id ? ' active' : ''}"
             onclick="setConstrCat('${c.id}')">${c.label}</button>`
  ).join('');
}

function setConstrCat(cat) {
  constrActiveCategory = cat;
  renderConstrCats();
  renderConstrItems();
}

function filterConstrItems() {
  renderConstrItems();
}

function renderConstrItems() {
  const query = (document.getElementById('constrSearch').value || '').toLowerCase().trim();
  const items = constrIngredients.filter(i => {
    if (constrActiveCategory !== 'all' && i.category !== constrActiveCategory) return false;
    if (query && !i.name.toLowerCase().includes(query)) return false;
    return true;
  });

  if (!items.length) {
    document.getElementById('constrItems').innerHTML =
      `<div class="constr-no-results">Ничего не найдено</div>`;
    return;
  }

  document.getElementById('constrItems').innerHTML = items.map(item => {
    const added = !!constrSelected[item.id];
    const n = item.per100g;
    return `<div class="constr-item${added ? ' added' : ''}" onclick="toggleConstrIngredient('${item.id}')">
      <div class="constr-item-name">${escHtml(item.name)}</div>
      <div class="constr-item-kbju">${n.kcal} ккал · ${n.protein}б · ${n.fat}ж · ${n.carbs}у</div>
      <div class="constr-item-check">✓</div>
    </div>`;
  }).join('');
}

function toggleConstrIngredient(id) {
  if (constrSelected[id]) {
    delete constrSelected[id];
  } else {
    const item = constrIngredients.find(i => i.id === id);
    if (item) constrSelected[id] = item.default_g;
  }
  renderConstrItems();
  renderConstrDish();
}

function removeConstrIngredient(id) {
  delete constrSelected[id];
  renderConstrItems();
  renderConstrDish();
}

function updateConstrGrams(id, val) {
  constrSelected[id] = Math.max(0, parseFloat(val) || 0);
  updateConstrKbju();
  renderConstrDishItemKbju(id);
  renderConstrStores();
}

function renderConstrDish() {
  const emptyEl = document.getElementById('constrDishEmpty');
  const listEl = document.getElementById('constrDishList');
  const storesEl = document.getElementById('constrStores');
  const ids = Object.keys(constrSelected);

  if (!ids.length) {
    emptyEl.style.display = 'block';
    listEl.innerHTML = '';
    storesEl.style.display = 'none';
    updateConstrKbju();
    return;
  }

  emptyEl.style.display = 'none';
  storesEl.style.display = 'block';

  listEl.innerHTML = ids.map(id => {
    const item = constrIngredients.find(i => i.id === id);
    if (!item) return '';
    const g = constrSelected[id];
    return `<div class="constr-dish-item" id="constr-dish-item-${id}">
      <div class="constr-dish-item-top">
        <span class="constr-dish-item-name">${escHtml(item.name)}</span>
        <button class="constr-dish-remove" onclick="removeConstrIngredient('${id}')">✕</button>
      </div>
      <div class="constr-dish-item-bottom">
        <div class="constr-gram-wrap">
          <input class="constr-gram-input" type="number" value="${g}" min="1" max="2000"
                 oninput="updateConstrGrams('${id}', this.value)" />
          <span class="constr-gram-unit">г</span>
        </div>
        <div class="constr-dish-item-kbju" id="constr-kbju-${id}">${calcConstrItemKbjuHtml(item, g)}</div>
      </div>
    </div>`;
  }).join('');

  updateConstrKbju();
  renderConstrStores();
}

function calcConstrItemKbjuHtml(item, g) {
  const k = g / 100;
  const kcal = Math.round(item.per100g.kcal * k);
  const p = Math.round(item.per100g.protein * k * 10) / 10;
  const f = Math.round(item.per100g.fat * k * 10) / 10;
  const c = Math.round(item.per100g.carbs * k * 10) / 10;
  return `<span class="kbju-pill kcal">${kcal} ккал</span>
          <span class="kbju-pill">${p}б</span>
          <span class="kbju-pill">${f}ж</span>
          <span class="kbju-pill">${c}у</span>`;
}

function renderConstrDishItemKbju(id) {
  const el = document.getElementById(`constr-kbju-${id}`);
  if (!el) return;
  const item = constrIngredients.find(i => i.id === id);
  if (!item) return;
  el.innerHTML = calcConstrItemKbjuHtml(item, constrSelected[id] || 0);
}

function updateConstrKbju() {
  let kcal = 0, protein = 0, fat = 0, carbs = 0;
  for (const [id, g] of Object.entries(constrSelected)) {
    const item = constrIngredients.find(i => i.id === id);
    if (!item) continue;
    const k = g / 100;
    kcal    += item.per100g.kcal    * k;
    protein += item.per100g.protein * k;
    fat     += item.per100g.fat     * k;
    carbs   += item.per100g.carbs   * k;
  }
  document.getElementById('constrKcal').textContent    = Math.round(kcal);
  document.getElementById('constrProtein').textContent = (Math.round(protein * 10) / 10) + ' г';
  document.getElementById('constrFat').textContent     = (Math.round(fat     * 10) / 10) + ' г';
  document.getElementById('constrCarbs').textContent   = (Math.round(carbs   * 10) / 10) + ' г';
}

function renderConstrStores() {
  const ids = Object.keys(constrSelected);
  document.getElementById('constrStoresLinks').innerHTML = ids.map(id => {
    const item = constrIngredients.find(i => i.id === id);
    if (!item) return '';
    const q = encodeURIComponent(item.name);
    return `<div class="constr-store-item">
      <span class="constr-store-ing">${escHtml(item.name)}</span>
      <div class="constr-store-links">
        <a href="https://vkusvill.ru/search/?text=${q}" target="_blank" rel="noopener" class="constr-store-link">ВкусВилл</a>
        <a href="https://samokat.ru/search?q=${q}" target="_blank" rel="noopener" class="constr-store-link">Самокат</a>
      </div>
    </div>`;
  }).join('');
}

function clearConstructor() {
  constrSelected = {};
  renderConstrItems();
  renderConstrDish();
}

"use strict";
// Минимальный SPA-клиент к REST API (/api). Токен хранится в localStorage.
const $ = (s, r = document) => r.querySelector(s);
const app = $("#app");
let token = localStorage.getItem("token");
let me = null;

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = s => new Date(s).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
const LEVEL = { beginner: "Начальный", intermediate: "Средний", advanced: "Продвинутый" };
const FORMAT = { online: "Онлайн", offline: "Очно", both: "Онлайн и очно" };
const STATUS = { pending: "Ожидает", accepted: "Принята", rejected: "Отклонена", cancelled: "Отменена", completed: "Завершена" };
const OSTATUS = { active: "Активно", archived: "В архиве", hidden: "Скрыто модератором" };
const opts = (obj, sel) => Object.entries(obj).map(([k, v]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${v}</option>`).join("");
const flat = v => Array.isArray(v) ? v.map(flat).join(" ") : (v && typeof v === "object") ? Object.values(v).map(flat).join(" ") : String(v);

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Token " + token } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* пустой ответ */ }
  if (res.status === 401 && token) { clearAuth(); }
  if (!res.ok) throw { status: res.status, data };
  return data;
}
const msg = e => (e && e.data && e.data.detail) || "Что-то пошло не так.";
function toast(text) { const t = $("#toast"); t.textContent = text; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 3500); }

function showErrors(form, e) {
  form.querySelectorAll("[data-err]").forEach(n => n.textContent = "");
  const box = form.querySelector(".form-msg");
  if (box) { box.textContent = ""; box.className = "form-msg err"; }
  const errs = e.data && e.data.errors;
  const general = [];
  if (errs && !Array.isArray(errs) && typeof errs === "object") {
    for (const [k, v] of Object.entries(errs)) {
      const t = form.querySelector(`[data-err="${k}"]`);
      if (t) t.textContent = flat(v); else general.push(flat(v));
    }
  } else if (errs) general.push(flat(errs));
  if (!errs) general.push(msg(e));
  if (box) box.textContent = general.join(" ");
}
function done(form, text) { const b = form.querySelector(".form-msg"); if (b) { b.className = "form-msg ok"; b.textContent = text; } }
const field = (name, label, input) => `<label>${label}${input}<small class="err" data-err="${name}"></small></label>`;
const formEnd = label => `<button>${label}</button><span class="form-msg err"></span>`;

// ---------- авторизация ----------
function clearAuth() { token = null; me = null; localStorage.removeItem("token"); }
async function logout() { try { await api("/auth/logout", { method: "POST" }); } catch { } clearAuth(); location.hash = "#/"; route(); }
function need() { if (!me) { location.hash = "#/login"; throw { status: -1 }; } }

function renderNav() {
  $("#nav").innerHTML = `<a href="#/">Каталог</a>` + (me
    ? `<a href="#/my-offers">Мои предложения</a><a href="#/meetings">Мои встречи</a>`
      + (me.is_moderator ? `<a href="#/moderation">Модерация</a>` : "")
      + `<a href="#/profile">${esc(me.first_name || me.username)}</a><a href="#" data-act="logout">Выйти</a>`
    : `<a href="#/login">Войти</a><a href="#/register">Регистрация</a>`);
}

// ---------- маршрутизация ----------
const routes = [
  [/^#\/?$/, catalog], [/^#\/offer\/(\d+)$/, offerPage], [/^#\/user\/(\d+)$/, userPage],
  [/^#\/my-offers$/, myOffers], [/^#\/meetings$/, meetings], [/^#\/moderation$/, moderation],
  [/^#\/profile$/, profile], [/^#\/login$/, loginPage], [/^#\/register$/, registerPage],
];
async function route() {
  renderNav();
  const h = location.hash || "#/";
  for (const [re, fn] of routes) {
    const m = h.match(re);
    if (m) {
      app.innerHTML = '<p class="muted">Загрузка…</p>';
      try { await fn(...m.slice(1)); } catch (e) { if (e.status !== -1) errorPage(e); }
      return;
    }
  }
  errorPage({ status: 404 });
}
function errorPage(e) {
  if (e.status === 401) { location.hash = "#/login"; return; }
  const [code, text] = e.status === 403 ? ["403", "Доступ запрещён"] : e.status === 404 ? ["404", "Страница не найдена"] : ["Ошибка", msg(e)];
  app.innerHTML = `<h2>${code}</h2><p>${esc(text)}</p><a href="#/">В каталог</a>`;
}

// ---------- каталог ----------
const cf = { q: "", category: "", level: "", format: "", page: 1, fav: false };
const offerCard = o => `<article class="card">
  <h3><a href="#/offer/${o.id}">${esc(o.title)}</a></h3>
  <p class="muted">${esc(o.category_name)} · ${LEVEL[o.level]} · ${o.duration_minutes} мин · ${FORMAT[o.format]} · <a href="#/user/${o.author}">${esc(o.author_name)}</a></p>
  <p>${esc(o.description.slice(0, 160))}</p>
  <p class="muted">Свободных слотов: ${o.slots.filter(s => !s.is_taken).length}</p>
  ${me && me.id !== o.author ? `<button class="sec" data-act="fav" data-id="${o.id}" data-on="${o.is_favorite ? 1 : 0}">${o.is_favorite ? "★ В избранном" : "☆ В избранное"}</button>` : ""}
</article>`;

async function catalog() {
  const cats = await api("/categories");
  app.innerHTML = `<h2>Каталог</h2>
  <form id="f" class="filters">
    <input name="q" placeholder="Поиск по теме" value="${esc(cf.q)}">
    <select name="category"><option value="">Все категории</option>${cats.map(c => `<option value="${c.id}" ${cf.category == c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
    <select name="level"><option value="">Любой уровень</option>${opts(LEVEL, cf.level)}</select>
    <select name="format"><option value="">Любой формат</option>${opts({ online: "Онлайн", offline: "Очно" }, cf.format)}</select>
    ${me ? `<label><input type="checkbox" name="fav" ${cf.fav ? "checked" : ""}> Избранное</label>` : ""}
    <button>Найти</button></form>
  <div id="list"></div><div id="pager" class="row"></div>`;
  $("#f").onsubmit = e => {
    e.preventDefault();
    const d = new FormData(e.target);
    Object.assign(cf, { q: d.get("q"), category: d.get("category"), level: d.get("level"), format: d.get("format"), fav: d.get("fav") === "on", page: 1 });
    loadList();
  };
  loadList();
}
async function loadList() {
  const list = $("#list"), pager = $("#pager");
  list.innerHTML = '<p class="muted">Загрузка…</p>'; pager.innerHTML = "";
  const p = new URLSearchParams({ page: cf.page });
  for (const k of ["q", "category", "level", "format"]) if (cf[k]) p.set(k, cf[k]);
  try {
    const d = await api((cf.fav ? "/offers/favorites" : "/offers") + "?" + p);
    list.innerHTML = d.results.length ? d.results.map(offerCard).join("") : '<p class="muted">Ничего не найдено.</p>';
    if (d.previous || d.next) pager.innerHTML = `<button class="sec" data-act="page" data-p="${cf.page - 1}" ${d.previous ? "" : "disabled"}>← Назад</button>
      <span class="muted">стр. ${cf.page} из ${Math.ceil(d.count / 10)}</span>
      <button class="sec" data-act="page" data-p="${cf.page + 1}" ${d.next ? "" : "disabled"}>Вперёд →</button>`;
  } catch (e) { list.innerHTML = `<p class="err">${esc(msg(e))}</p>`; }
}

// ---------- карточка предложения ----------
async function offerPage(id) {
  const o = await api("/offers/" + id);
  const own = me && me.id === o.author;
  const slots = o.slots.length ? o.slots.map(s => `<div class="slot">
      <b>${fmtDate(s.start_at)}</b> — ${fmtDate(s.end_at)}
      ${s.is_taken ? '<span class="tag">занят</span>' : !me ? '<span class="muted"> · <a href="#/login">войдите</a>, чтобы записаться</span>'
        : own ? "" : o.status !== "active" ? "" : `<form data-form="req" data-slot="${s.id}">
          ${field("message", "", '<textarea name="message" placeholder="Сообщение автору (необязательно)"></textarea>')}${formEnd("Записаться")}</form>`}
    </div>`).join("") : '<p class="muted">Свободных слотов нет.</p>';
  app.innerHTML = `<article class="card"><h2>${esc(o.title)} ${o.status !== "active" ? `<span class="tag">${OSTATUS[o.status]}</span>` : ""}</h2>
    <p class="muted">${esc(o.category_name)} · ${LEVEL[o.level]} · ${o.duration_minutes} мин · ${FORMAT[o.format]} · <a href="#/user/${o.author}">${esc(o.author_name)}</a></p>
    ${o.hidden_reason ? `<p class="err">Причина скрытия: ${esc(o.hidden_reason)}</p>` : ""}
    <p>${esc(o.description)}</p>
    ${me && !own ? `<button class="sec" data-act="fav" data-id="${o.id}" data-on="${o.is_favorite ? 1 : 0}">${o.is_favorite ? "★ В избранном" : "☆ В избранное"}</button>` : ""}
  </article><h3>Слоты</h3><div class="card">${slots}</div>
  ${me && !own ? `<h3>Пожаловаться</h3><form class="inline" data-form="report" data-id="${o.id}">
    <input name="reason" placeholder="Причина жалобы"><button class="danger">Отправить</button><small class="err" data-err="reason"></small><span class="form-msg err"></span></form>` : ""}`;
}

async function userPage(id) {
  const u = await api("/users/" + id);
  app.innerHTML = `<h2>${esc(u.name)}</h2>
    <p class="muted">Формат: ${FORMAT[u.format]} · Рейтинг: ${u.rating ? u.rating.toFixed(1) + " / 5" : "нет отзывов"}</p>
    <p>${esc(u.bio) || '<span class="muted">Описание не заполнено.</span>'}</p>
    <p>${u.skills.map(s => `<span class="tag">${esc(s)}</span>`).join(" ")}</p>
    <p class="muted">${u.contact_visible ? "Контакт: " + esc(u.contact) : "Контакты видны после подтверждённой встречи."}</p>
    <h3>Предложения</h3>${u.offers.map(offerCard).join("") || '<p class="muted">Нет активных предложений.</p>'}
    <h3>Отзывы</h3>${u.reviews.map(r => `<div class="card"><b>${"★".repeat(r.score)}</b> ${esc(r.text)}<div class="muted">${esc(r.author.name)} · ${esc(r.offer_title)}</div></div>`).join("") || '<p class="muted">Отзывов пока нет.</p>'}`;
}

// ---------- мои предложения ----------
async function myOffers() {
  need();
  const [cats, d] = await Promise.all([api("/categories"), api("/offers/mine?page_size=50")]);
  app.innerHTML = `<h2>Мои предложения</h2>
  <form data-form="offer" class="card">
    <h3>Новое предложение</h3>
    ${field("title", "Тема", '<input name="title" maxlength="120">')}
    ${field("category", "Категория", `<select name="category">${cats.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>`)}
    ${field("description", "Описание", '<textarea name="description"></textarea>')}
    ${field("level", "Уровень ученика", `<select name="level">${opts(LEVEL)}</select>`)}
    ${field("duration_minutes", "Длительность, мин", '<input name="duration_minutes" type="number" value="60">')}
    ${field("format", "Формат", `<select name="format">${opts(FORMAT)}</select>`)}
    <div id="slots"><div class="row"><input type="datetime-local" name="start"><input type="datetime-local" name="end"></div></div>
    <small class="err" data-err="slots"></small>
    <button type="button" class="sec" data-act="addslot">+ слот</button>
    ${formEnd("Опубликовать")}
  </form>
  ${d.results.length ? d.results.map(o => `<article class="card"><h3><a href="#/offer/${o.id}">${esc(o.title)}</a> <span class="tag">${OSTATUS[o.status]}</span></h3>
    <p class="muted">${esc(o.category_name)} · ${LEVEL[o.level]} · ${o.duration_minutes} мин · слотов впереди: ${o.slots.length}</p>
    ${o.hidden_reason ? `<p class="err">Причина скрытия: ${esc(o.hidden_reason)}</p>` : ""}
    ${o.status === "active" ? `<div class="row"><button class="sec" data-act="edit" data-id="${o.id}" data-title="${esc(o.title)}" data-desc="${esc(o.description)}">Изменить</button>
      <button class="danger" data-act="archive" data-id="${o.id}">В архив</button></div>` : ""}</article>`).join("") : '<p class="muted">У вас пока нет предложений.</p>'}`;
}

// ---------- встречи и чат ----------
let mbox = "incoming";
const reqs = {};
async function meetings() {
  need();
  const d = await api(`/requests?box=${mbox}&page_size=50`);
  d.results.forEach(r => reqs[r.id] = r);
  app.innerHTML = `<h2>Мои встречи</h2>
  <div class="tabs"><button class="${mbox === "incoming" ? "" : "sec"}" data-act="box" data-b="incoming">Входящие</button>
    <button class="${mbox === "outgoing" ? "" : "sec"}" data-act="box" data-b="outgoing">Исходящие</button></div>
  ${d.results.length ? d.results.map(r => {
    const inc = mbox === "incoming", st = r.status;
    return `<article class="card"><h3><a href="#/offer/${r.offer.id}">${esc(r.offer.title)}</a> <span class="tag">${STATUS[st]}</span></h3>
      <p class="muted">${fmtDate(r.slot.start_at)} · ${inc ? "ученик: " + esc(r.student.name) : "автор: " + esc(r.offer.author_name)}</p>
      ${r.message ? `<p>«${esc(r.message)}»</p>` : ""}
      <div class="row">
        ${inc && st === "pending" ? `<button data-act="req" data-do="accept" data-id="${r.id}">Принять</button><button class="sec" data-act="req" data-do="reject" data-id="${r.id}">Отклонить</button>` : ""}
        ${inc && st === "accepted" ? `<button data-act="req" data-do="complete" data-id="${r.id}">Завершить</button>` : ""}
        ${((!inc && st === "pending") || st === "accepted") ? `<button class="danger" data-act="req" data-do="cancel" data-id="${r.id}">Отменить</button>` : ""}
        ${st === "accepted" || st === "completed" ? `<button class="sec" data-act="chat" data-id="${r.id}">Чат</button>` : ""}
      </div><div id="chat-${r.id}"></div></article>`;
  }).join("") : '<p class="muted">Заявок пока нет.</p>'}`;
}
async function openChat(id) {
  const box = $("#chat-" + id), r = reqs[id];
  try {
    const d = await api(`/requests/${id}/messages`);
    const canReview = mbox === "outgoing" && r.status === "completed" && !r.has_review;
    box.innerHTML = `<div class="chat">${d.results.map(m => `<div class="msg"><b>${esc(m.sender.name)}</b> <span class="muted">${fmtDate(m.created_at)}</span><br>${esc(m.text)}</div>`).join("") || '<p class="muted">Сообщений пока нет.</p>'}
      <form class="inline" data-form="msg" data-id="${id}"><input name="text" placeholder="Сообщение" style="flex:1"><button>Отправить</button><small class="err" data-err="text"></small><span class="form-msg err"></span></form></div>
      ${canReview ? `<form data-form="review" data-id="${id}" class="chat"><b>Оставить отзыв</b>
        ${field("score", "Оценка", '<select name="score">' + [5, 4, 3, 2, 1].map(n => `<option>${n}</option>`).join("") + "</select>")}
        ${field("text", "Комментарий", '<textarea name="text"></textarea>')}${formEnd("Отправить отзыв")}</form>` : ""}`;
  } catch (e) { box.innerHTML = `<p class="err">${esc(msg(e))}</p>`; }
}

// ---------- модерация ----------
async function moderation() {
  need();
  const d = await api("/moderation/reports?page_size=50"); // 403 для обычного участника → страница 403
  app.innerHTML = `<h2>Модерация: жалобы</h2>` + (d.results.length ? d.results.map(r => `<article class="card">
    <h3><a href="#/offer/${r.offer.id}">${esc(r.offer.title)}</a> <span class="tag">${OSTATUS[r.offer.status]}</span></h3>
    <p class="muted">Жалоба от ${esc(r.reporter.name)}, ${fmtDate(r.created_at)}</p><p>${esc(r.reason)}</p>
    <form class="inline" data-form="resolve" data-id="${r.id}"><input name="reason" placeholder="Причина решения" style="flex:1">
      <button name="action" value="hide" class="danger">Скрыть</button><button name="action" value="dismiss" class="sec">Отклонить жалобу</button>
      <small class="err" data-err="reason"></small><span class="form-msg err"></span></form></article>`).join("") : '<p class="muted">Открытых жалоб нет.</p>');
}

// ---------- профиль, вход, регистрация ----------
async function profile() {
  need();
  me = await api("/me");
  app.innerHTML = `<h2>Мой профиль</h2><form data-form="profile" class="card">
    ${field("first_name", "Имя", `<input name="first_name" value="${esc(me.first_name)}">`)}
    ${field("bio", "О себе", `<textarea name="bio">${esc(me.bio)}</textarea>`)}
    ${field("skills", "Навыки (через запятую)", `<input name="skills" value="${esc(me.skills.join(", "))}">`)}
    ${field("format", "Удобный формат", `<select name="format">${opts(FORMAT, me.format)}</select>`)}
    ${field("contact", "Контакт (виден только подтверждённому партнёру)", `<input name="contact" value="${esc(me.contact)}">`)}
    ${formEnd("Сохранить")}</form><p><a href="#/user/${me.id}">Как видят мой профиль другие</a></p>`;
}
function authForm(kind) {
  const reg = kind === "register";
  app.innerHTML = `<h2>${reg ? "Регистрация" : "Вход"}</h2><form data-form="${kind}" class="card" style="max-width:380px">
    ${field("username", "Логин", '<input name="username" autocomplete="username">')}
    ${reg ? field("first_name", "Имя", '<input name="first_name">') + field("email", "Email", '<input name="email" type="email">') : ""}
    ${field("password", "Пароль", `<input name="password" type="password" autocomplete="${reg ? "new" : "current"}-password">`)}
    ${formEnd(reg ? "Создать аккаунт" : "Войти")}</form>`;
}
async function loginPage() { authForm("login"); }
async function registerPage() { authForm("register"); }

// ---------- обработчики форм ----------
const forms = {
  async login(f, d) { await auth("/auth/login", Object.fromEntries(d)); },
  async register(f, d) { await auth("/auth/register", Object.fromEntries(d)); },
  async profile(f, d) {
    await api("/me", { method: "PATCH", body: { first_name: d.get("first_name"), bio: d.get("bio"), format: d.get("format"), contact: d.get("contact"),
      skills: d.get("skills").split(",").map(s => s.trim()).filter(Boolean) } });
    me = await api("/me"); renderNav(); done(f, "Сохранено");
  },
  async req(f, d) { await api(`/slots/${f.dataset.slot}/requests`, { method: "POST", body: { message: d.get("message") } }); toast("Заявка отправлена"); location.hash = "#/meetings"; },
  async report(f, d) { await api(`/offers/${f.dataset.id}/report`, { method: "POST", body: { reason: d.get("reason") } }); f.reset(); done(f, "Жалоба отправлена"); },
  async offer(f, d) {
    const st = d.getAll("start"), en = d.getAll("end"), slots = [];
    st.forEach((s, i) => { if (s && en[i]) slots.push({ start_at: new Date(s).toISOString(), end_at: new Date(en[i]).toISOString() }); });
    await api("/offers", { method: "POST", body: { title: d.get("title"), category: d.get("category"), description: d.get("description"), level: d.get("level"),
      duration_minutes: d.get("duration_minutes"), format: d.get("format"), slots } });
    toast("Предложение опубликовано"); route();
  },
  async msg(f, d) { await api(`/requests/${f.dataset.id}/messages`, { method: "POST", body: { text: d.get("text") } }); openChat(f.dataset.id); },
  async review(f, d) { await api(`/requests/${f.dataset.id}/review`, { method: "POST", body: { score: +d.get("score"), text: d.get("text") } }); toast("Отзыв сохранён"); route(); },
  async resolve(f, d, btn) { await api(`/moderation/reports/${f.dataset.id}/resolve`, { method: "POST", body: { action: btn.value, reason: d.get("reason") } }); route(); },
};
async function auth(path, body) {
  const r = await api(path, { method: "POST", body });
  token = r.token; localStorage.setItem("token", token); me = r.user;
  location.hash = "#/"; route();
}
app.addEventListener("submit", async e => {
  const f = e.target, h = forms[f.dataset.form];
  if (!h) return;
  e.preventDefault();
  try { await h(f, new FormData(f), e.submitter); } catch (err) { showErrors(f, err); }
});

// ---------- обработчики кнопок ----------
const actions = {
  logout: () => logout(),
  page: el => { cf.page = +el.dataset.p; loadList(); },
  box: el => { mbox = el.dataset.b; route(); },
  chat: el => openChat(el.dataset.id),
  addslot: () => $("#slots").insertAdjacentHTML("beforeend", '<div class="row"><input type="datetime-local" name="start"><input type="datetime-local" name="end"></div>'),
  async fav(el) { await api(`/offers/${el.dataset.id}/favorite`, { method: el.dataset.on === "1" ? "DELETE" : "POST" }); route(); },
  async archive(el) { if (confirm("Архивировать предложение? Новые заявки будут закрыты, существующие встречи сохранятся.")) { await api(`/offers/${el.dataset.id}/archive`, { method: "POST" }); route(); } },
  async edit(el) {
    const title = prompt("Тема:", el.dataset.title); if (title === null) return;
    const description = prompt("Описание:", el.dataset.desc); if (description === null) return;
    await api(`/offers/${el.dataset.id}`, { method: "PATCH", body: { title, description } }); route();
  },
  async req(el) { await api(`/requests/${el.dataset.id}/${el.dataset.do}`, { method: "POST" }); route(); },
};
document.addEventListener("click", async e => {
  const el = e.target.closest("[data-act]");
  if (!el || !actions[el.dataset.act]) return;
  e.preventDefault();
  try { await actions[el.dataset.act](el); } catch (err) { toast(msg(err) + (err.data && err.data.errors ? " " + flat(err.data.errors) : "")); route(); }
});

(async function init() {
  if (token) { try { me = await api("/me"); } catch { clearAuth(); } }
  window.addEventListener("hashchange", route);
  route();
})();

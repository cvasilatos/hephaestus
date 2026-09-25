"use strict";

const localDateTime = (value, options = {}) => new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium", timeStyle: "short", ...options,
}).format(new Date(value));

document.querySelectorAll("[data-local-time]").forEach((element) => {
  element.textContent = localDateTime(element.dataset.localTime);
});
document.querySelectorAll("[data-local-day]").forEach((element) => {
  element.textContent = new Intl.DateTimeFormat(undefined, { day: "2-digit" }).format(new Date(element.dataset.localDay));
});
document.querySelectorAll("[data-local-month]").forEach((element) => {
  element.textContent = new Intl.DateTimeFormat(undefined, { month: "short" }).format(new Date(element.dataset.localMonth));
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const reservationForm = document.querySelector("[data-reservation-form]");
if (reservationForm) {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Local time";
  document.querySelector("[data-browser-timezone]").textContent = zone;
  const visibleInputs = reservationForm.querySelectorAll("[data-utc-target]");
  const bookingPolicy = reservationForm.querySelector("[data-booking-policy]");
  const slotMinutes = Number(bookingPolicy.dataset.slotMinutes);
  const defaultMinutes = Number(bookingPolicy.dataset.defaultMinutes);
  const noticeMinutes = Number(bookingPolicy.dataset.noticeMinutes);
  const pad = (number) => String(number).padStart(2, "0");
  const asLocalInput = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const nextBoundary = () => {
    const target = new Date(Date.now() + noticeMinutes * 60000);
    const startOfDay = new Date(target);
    startOfDay.setHours(0, 0, 0, 0);
    const elapsedMinutes = (target - startOfDay) / 60000;
    const result = new Date(startOfDay);
    result.setMinutes(Math.ceil(elapsedMinutes / slotMinutes) * slotMinutes);
    return result;
  };
  visibleInputs.forEach((visible, index) => {
    visible.step = String(slotMinutes * 60);
    const hidden = document.getElementById(visible.dataset.utcTarget);
    if (hidden.value) visible.value = asLocalInput(new Date(hidden.value));
    else {
      const start = nextBoundary();
      visible.value = asLocalInput(index === 0 ? start : new Date(start.getTime() + defaultMinutes * 60000));
    }
  });
  reservationForm.addEventListener("submit", () => {
    visibleInputs.forEach((visible) => {
      const hidden = document.getElementById(visible.dataset.utcTarget);
      hidden.value = new Date(visible.value).toISOString();
    });
  });
}

const countdown = document.querySelector("[data-countdown]");
if (countdown) {
  const output = countdown.querySelector("[data-countdown-output]");
  const tick = () => {
    const seconds = Math.max(0, Math.floor((new Date(countdown.dataset.countdown) - Date.now()) / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    output.textContent = `${hours}h ${minutes}m remaining`;
  };
  tick(); window.setInterval(tick, 30000);
}

const eventData = document.getElementById("calendar-events");
if (eventData) {
  const events = JSON.parse(eventData.textContent);
  const week = document.getElementById("week-calendar");
  const list = document.getElementById("list-calendar");
  const dayStart = new Date(); dayStart.setHours(0, 0, 0, 0);
  const calendarDays = Number(week.dataset.calendarDays);
  week.style.setProperty("--calendar-columns", String(Math.min(calendarDays, 7)));
  const days = Array.from({ length: calendarDays }, (_, index) => new Date(dayStart.getTime() + index * 86400000));
  days.forEach((day) => {
    const column = document.createElement("section");
    column.className = "calendar-day";
    const heading = document.createElement("h3");
    heading.textContent = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(day);
    column.appendChild(heading);
    const dayEvents = events.filter((event) => new Date(event.startsAt) < new Date(day.getTime() + 86400000) && new Date(event.endsAt) > day);
    dayEvents.forEach((event) => {
      const block = document.createElement("article");
      block.className = `calendar-event ${event.kind}`;
      block.innerHTML = `<strong></strong><span></span><small></small>`;
      block.querySelector("strong").textContent = event.owner;
      block.querySelector("span").textContent = event.gpus.join(", ");
      block.querySelector("small").textContent = `${new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(event.startsAt))} – ${new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(event.endsAt))}`;
      column.appendChild(block);
    });
    if (!dayEvents.length) column.insertAdjacentHTML("beforeend", '<p class="day-empty">Open</p>');
    week.appendChild(column);
  });
  events.sort((a, b) => new Date(a.startsAt) - new Date(b.startsAt)).forEach((event) => {
    const row = document.createElement("article"); row.className = "list-event";
    row.innerHTML = "<div><strong></strong><span></span></div><time></time>";
    row.querySelector("strong").textContent = event.owner;
    row.querySelector("span").textContent = event.gpus.join(", ");
    row.querySelector("time").textContent = `${localDateTime(event.startsAt)} – ${localDateTime(event.endsAt)}`;
    list.appendChild(row);
  });
  document.querySelectorAll("[data-calendar-view]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll("[data-calendar-view]").forEach((item) => item.classList.toggle("active", item === button));
    week.hidden = button.dataset.calendarView !== "week";
    list.hidden = button.dataset.calendarView !== "list";
  }));
}

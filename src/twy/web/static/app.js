// Small progressive enhancements. Every page also works without JavaScript.
(() => {
  const band = (s) => (s === null ? "none" : s >= 0.85 ? "good" : s >= 0.65 ? "warn" : "bad");
  const setBand = (el, s) => {
    el.classList.remove("band-good", "band-warn", "band-bad", "band-none");
    el.classList.add(`band-${band(s)}`);
  };

  // − / + steppers on the weekly review.
  document.addEventListener("click", (e) => {
    const button = e.target.closest("[data-step]");
    if (!button) return;
    const input = button.parentElement.querySelector("input");
    const max = input.max === "" ? Infinity : Number(input.max);
    const next = (parseInt(input.value, 10) || 0) + Number(button.dataset.step);
    input.value = Math.max(0, Math.min(max, next));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });

  // Week chips: bring the selected week into view when the row scrolls.
  document.querySelector(".weekpicker .chip.on")?.scrollIntoView({ block: "nearest", inline: "center" });

  // Controls that only make sense with JavaScript.
  document.querySelectorAll("[data-js-only]").forEach((el) => (el.hidden = false));

  // Saved / warning messages float as toasts (see .js .toasts) and fade out. Re-adding them after load
  // makes screen readers announce them, since the role="status" region was already in the page.
  const toasts = document.querySelector("[data-toasts]");
  if (toasts) {
    const items = [...toasts.children];
    toasts.replaceChildren();
    setTimeout(() => toasts.append(...items), 50);
    items.forEach((item) => {
      const hide = () => {
        item.classList.add("leaving");
        setTimeout(() => item.remove(), 300);
      };
      item.addEventListener("click", hide);
      setTimeout(hide, item.classList.contains("flash-warn") ? 7000 : 4000);
    });
  }

  // Calendar time slots: add a row (next day, same time and length) or remove one.
  document.addEventListener("click", (e) => {
    const add = e.target.closest("[data-add-slot]");
    if (add) {
      const box = add.closest("[data-slots]");
      const rows = box.querySelector("[data-slot-rows]");
      const row = box.querySelector("[data-slot-template]").content.firstElementChild.cloneNode(true);
      const last = rows.lastElementChild;
      if (last) {
        const field = (el, name) => el.querySelector(`[name="${name}"]`);
        field(row, "slot_day").value = (Number(field(last, "slot_day").value) + 1) % 7;
        field(row, "slot_time").value = field(last, "slot_time").value;
        field(row, "slot_minutes").value = field(last, "slot_minutes").value;
      }
      rows.append(row);
      row.querySelector("select").focus();
      box.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const remove = e.target.closest("[data-remove-slot]");
    if (remove) {
      const box = remove.closest("[data-slots]");
      remove.closest(".slot-row").remove();
      box.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    // Copy the calendar link. navigator.clipboard needs HTTPS, so fall back to execCommand on the LAN.
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      const input = document.querySelector(copy.dataset.copy);
      input.select();
      const done = () => (copy.textContent = "Copied");
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(input.value).then(done);
      else if (document.execCommand("copy")) done();
    }
  });

  // Calendar clashes: the server checks (one rule, in Python) and we show the answer under each row.
  // One check covers the whole form, so a week's plan compares every tactic's rows with each other.
  document.querySelectorAll("[data-check-url]").forEach((box) => {
    const form = box.closest("form");
    const rows = () => form.querySelectorAll("[data-slot-rows] > .slot-row");
    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      node.className = className;
      node.textContent = text;
      return node;
    };
    const show = (row, result) => {
      row.querySelector(".slot-note")?.remove();
      if (!result) return;
      const note = el("div", "slot-note", "");
      note.setAttribute("role", "status");
      result.clashes.forEach((text) => note.append(el("p", "", `⚠ ${text}`)));
      const fixes = el("p", "slot-fixes", "");
      [result.earlier, result.later].filter(Boolean).forEach((time) => {
        const move = el("button", "btn small", `Move to ${time}`);
        move.type = "button";
        move.dataset.moveTo = time;
        fixes.append(move);
      });
      if (result.no_gap) fixes.append(el("span", "muted", result.no_gap));
      note.append(fixes);
      row.append(note);
    };
    let timer;
    let latest = 0;
    const check = async () => {
      const ticket = ++latest;
      let data;
      try {
        const response = await fetch(box.dataset.checkUrl, { method: "POST", body: new FormData(form) });
        data = await response.json();
      } catch {
        return; // offline, logged out or restarting: hints are optional
      }
      if (ticket !== latest) return; // an older answer arriving after a newer one
      rows().forEach((row, i) => show(row, data.rows[i]));
      Object.entries(data.placed || {}).forEach(([id, n]) => {
        form.querySelectorAll(`[data-placed-for="${id}"]`).forEach((count) => (count.textContent = n));
      });
    };
    const soon = () => {
      clearTimeout(timer);
      timer = setTimeout(check, 300);
    };
    form.addEventListener("input", soon);
    form.addEventListener("change", soon);
    form.addEventListener("click", (e) => {
      const move = e.target.closest("[data-move-to]");
      if (!move) return;
      move.closest(".slot-row").querySelector('[name="slot_time"]').value = move.dataset.moveTo;
      check();
    });
    check();
  });

  // Confirm destructive actions (delete, close cycle).
  document.addEventListener("submit", (e) => {
    const message = e.submitter?.dataset.confirm || e.target.dataset.confirm;
    if (message && !window.confirm(message)) e.preventDefault();
  });

  // Live execution score while filling in the scorecard.
  const pill = document.querySelector("[data-live-score]");
  if (pill) {
    const update = () => {
      let planned = 0;
      let done = 0;
      document.querySelectorAll("[data-planned]").forEach((row) => {
        const p = Number(row.dataset.planned);
        const d = parseInt(row.querySelector("input").value, 10) || 0;
        planned += p;
        done += Math.max(0, Math.min(d, p));
      });
      const score = planned ? done / planned : null;
      pill.querySelector("[data-value]").textContent = score === null ? "—" : `${Math.round(score * 100)}%`;
      setBand(pill, score);
    };
    document.addEventListener("input", update);
  }

  // Today: log a tactic without reloading the page (the form still works without JavaScript).
  // The count changes on tap; requests for a row go one at a time, and the server's answer
  // is the truth once the row has nothing left in flight. A failed tap is undone in place
  // with a Retry, so the page never reloads and typed reflection text is never lost.
  const setRow = (row, today, week) => {
    row.querySelector("[data-today]").textContent = today;
    row.querySelector("[data-week]").textContent = week;
    row.classList.toggle("zero", today === 0);
    row.classList.toggle("met", week >= Number(row.dataset.planned));
  };
  const updateProgress = () => {
    const pill = document.querySelector("[data-week-progress]");
    let planned = 0;
    let done = 0;
    document.querySelectorAll(".log-row").forEach((row) => {
      const p = Number(row.dataset.planned);
      planned += p;
      done += Math.min(Number(row.querySelector("[data-week]").textContent), p);
    });
    const score = planned ? done / planned : null;
    pill.querySelector("[data-value]").textContent = score === null ? "—" : `${Math.round(score * 100)}%`;
    setBand(pill, score);
  };
  const showLogError = (row, text, retry) => {
    row.querySelector(".log-error")?.remove();
    const note = document.createElement("p");
    note.className = "log-error";
    note.setAttribute("role", "alert");
    note.textContent = `${text} `;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = retry ? "Retry" : "Reload";
    button.addEventListener("click", () => {
      note.remove();
      if (retry) retry();
      else window.location.reload();
    });
    note.append(button);
    row.querySelector(".tactic").append(note);
  };
  const rowQueue = new WeakMap();
  document.addEventListener("submit", (e) => {
    const form = e.target.closest("[data-log]");
    if (!form) return;
    e.preventDefault();
    const row = form.closest(".log-row");
    const delta = Number(form.querySelector('[name="delta"]').value);
    const today = Number(row.querySelector("[data-today]").textContent);
    if (today + delta < 0) return;
    setRow(row, today + delta, Number(row.querySelector("[data-week]").textContent) + delta);
    updateProgress();
    row.querySelector(".log-error")?.remove();
    row.dataset.inFlight = Number(row.dataset.inFlight || 0) + 1;
    const body = new FormData(form);
    const send = async () => {
      let response;
      try {
        response = await fetch(form.action, { method: "POST", body, headers: { Accept: "application/json" } });
      } catch {
        return { failed: "Couldn't save: no connection." };
      }
      try {
        if (!response.ok) throw new Error(response.statusText);
        return { data: await response.json() };
      } catch {
        // Expired form or logged out: nothing was saved, and a reload shows why.
        return { failed: "Couldn't save.", reload: true };
      }
    };
    const run = (rowQueue.get(row) || Promise.resolve()).then(send).then((result) => {
      const left = Number(row.dataset.inFlight) - 1;
      row.dataset.inFlight = left;
      if (result.data) {
        if (left === 0) setRow(row, result.data.today, result.data.week);
      } else {
        // Undo this tap. If it did reach the server, the next successful tap corrects the count.
        const t = Number(row.querySelector("[data-today]").textContent);
        const w = Number(row.querySelector("[data-week]").textContent);
        setRow(row, Math.max(0, t - delta), Math.max(0, w - delta));
        showLogError(row, result.failed, result.reload ? null : () => form.requestSubmit());
      }
      updateProgress();
    });
    rowQueue.set(row, run);
  });

  // Warn before leaving a page with typed-but-unsaved reflection, review or vision text.
  const guarded = [...document.querySelectorAll("form[data-guard]")].map((form) => ({
    form,
    saved: new URLSearchParams(new FormData(form)).toString(),
  }));
  let submitting = false;
  document.addEventListener("submit", (e) => {
    if (!e.defaultPrevented && e.target.matches("form[data-guard]")) submitting = true;
  });
  window.addEventListener("beforeunload", (e) => {
    if (submitting) return;
    if (guarded.some(({ form, saved }) => new URLSearchParams(new FormData(form)).toString() !== saved)) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // Plan: ↑ / ↓ reorder in place. The server swaps positions within the same list, so do the same here.
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-move]");
    if (!form) return;
    e.preventDefault();
    const item = form.closest("[data-item]");
    const delta = Number(form.querySelector('[name="delta"]').value);
    let response;
    try {
      response = await fetch(form.action, { method: "POST", body: new FormData(form), headers: { Accept: "application/json" } });
    } catch {
      form.submit(); // offline: let the browser report it
      return;
    }
    if (!response.ok) {
      form.submit(); // expired form or logged out: post normally to see why
      return;
    }
    const kind = item.dataset.item;
    let other = delta < 0 ? item.previousElementSibling : item.nextElementSibling;
    while (other && other.dataset.item !== kind) other = delta < 0 ? other.previousElementSibling : other.nextElementSibling;
    if (!other) return; // already first or last
    if (delta < 0) other.before(item);
    else other.after(item);
    form.querySelector("button").focus();
    item.classList.remove("moved");
    void item.offsetWidth; // restart the highlight
    item.classList.add("moved");
  });

  // New cycle: "This week" / "Next week" fill in the start date, and show which one is chosen.
  const picks = document.querySelectorAll("[data-set-date]");
  if (picks.length) {
    const input = picks[0].form.querySelector('[name="start"]');
    const mark = () => picks.forEach((p) => p.setAttribute("aria-pressed", String(p.dataset.setDate === input.value)));
    picks.forEach((pick) =>
      pick.addEventListener("click", () => {
        input.value = pick.dataset.setDate;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }),
    );
    input.addEventListener("input", mark);
    mark();
  }

  // Week: tapping a session shows its details, with Edit, instead of jumping straight into the form.
  const pop = document.getElementById("session-pop");
  if (pop && "popover" in HTMLElement.prototype) {
    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      node.textContent = text;
      return node;
    };
    document.addEventListener("click", (e) => {
      const session = e.target.closest("[data-session]");
      if (!session || e.metaKey || e.ctrlKey) return;
      e.preventDefault();
      const d = session.dataset;
      const close = el("button", "btn", "Close");
      close.type = "button";
      close.addEventListener("click", () => pop.hidePopover());
      const edit = el("a", "btn primary", d.editLabel);
      edit.href = session.getAttribute("href");
      const buttons = el("div", "row-buttons", "");
      buttons.append(close, edit);
      pop.replaceChildren(el("h2", "", d.title), el("p", "", d.when), el("p", "muted small", d.what), buttons);
      pop.showPopover();
      edit.focus();
    });
  }

  // Ratings and yes/no: tap the chosen answer again to clear it.
  document.querySelectorAll("[data-clearable]").forEach((group) => {
    let before = null;
    group.addEventListener("pointerdown", (e) => {
      before = e.target.closest("label")?.querySelector("input:checked") ?? null;
    });
    group.addEventListener("click", (e) => {
      const input = e.target.closest("input");
      if (input && input === before) input.checked = false;
      before = null;
    });
  });

  // Week 13: show hit / missed as the final value is typed (same rule as scoring.goal_outcome).
  document.querySelectorAll("[data-final]").forEach((input) => {
    const row = input.closest("[data-target]");
    const tag = row.querySelector("[data-outcome]");
    const target = Number(row.dataset.target);
    const baseline = row.dataset.baseline === "" ? null : Number(row.dataset.baseline);
    input.addEventListener("input", () => {
      const text = input.value.trim();
      const value = Number(text);
      tag.classList.remove("good", "bad");
      if (text === "" || Number.isNaN(value)) {
        tag.textContent = "—";
        return;
      }
      const decreasing = baseline !== null && target < baseline;
      const hit = decreasing ? value <= target : value >= target;
      tag.textContent = hit ? "✓ hit" : "✗ missed";
      tag.classList.add(hit ? "good" : "bad");
    });
  });

  // New cycle: preview where the start date snaps to and when week 12 ends.
  const start = document.querySelector("[data-snap]");
  if (start) {
    const preview = document.querySelector("[data-snap-preview]");
    const weekStart = Number(start.dataset.weekStart); // 0 = Monday, like Python
    const fmt = (d) =>
      d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
    const update = () => {
      const d = new Date(`${start.value}T00:00:00Z`);
      if (Number.isNaN(d.getTime())) {
        preview.textContent = "Pick a start date.";
        return;
      }
      const weekday = (d.getUTCDay() + 6) % 7;
      d.setUTCDate(d.getUTCDate() + ((weekStart - weekday + 7) % 7));
      const end = new Date(d);
      end.setUTCDate(end.getUTCDate() + 83);
      preview.textContent = `Runs ${fmt(d)} → ${fmt(end)}, then a week 13 review.`;
    };
    start.addEventListener("input", update);
    update();
  }
})();

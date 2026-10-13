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
  document.querySelectorAll("[data-check-url]").forEach((box) => {
    const form = box.closest("form");
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
      box.querySelectorAll("[data-slot-rows] > .slot-row").forEach((row, i) => show(row, data.rows[i]));
    };
    const soon = () => {
      clearTimeout(timer);
      timer = setTimeout(check, 300);
    };
    form.addEventListener("input", soon);
    form.addEventListener("change", soon);
    box.addEventListener("click", (e) => {
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
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-log]");
    if (!form) return;
    e.preventDefault();
    let data;
    try {
      const response = await fetch(form.action, { method: "POST", body: new FormData(form), headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(response.statusText);
      data = await response.json();
    } catch {
      form.submit(); // fall back to a normal post and reload
      return;
    }
    const row = form.closest(".log-row");
    row.querySelector("[data-today]").textContent = data.today;
    row.querySelector("[data-week]").textContent = data.week;
    row.classList.toggle("met", data.week >= Number(row.dataset.planned));
    const pill = document.querySelector("[data-week-progress]");
    pill.querySelector("[data-value]").textContent = data.progress;
    pill.classList.remove("band-good", "band-warn", "band-bad", "band-none");
    pill.classList.add(`band-${data.band}`);
  });

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

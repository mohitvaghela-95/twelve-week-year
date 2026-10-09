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

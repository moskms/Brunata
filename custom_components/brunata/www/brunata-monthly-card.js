/**
 * Brunata monthly consumption card — Del 3b.
 *
 * Plain vanilla-JS custom element, no build step / no external imports:
 * one vertical bar-list per active meter (grouped by allocationUnit), each
 * with its own year dropdown (populated from that meter's own available
 * years), showing Januar-December for the selected year plus a Total row,
 * year-over-year %% colored (green=less, red=more), click a month to show
 * its daily consumption chart (plain SVG, no chart library) in a shared
 * detail panel to the right of the table; click the same month again to
 * close it.
 *
 * Registered as a frontend resource via custom_components/brunata/__init__.py
 * (async_register_static_paths + frontend.add_extra_js_url) — add it to a
 * dashboard as `type: custom:brunata-monthly-card`.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

const MONTH_NAMES_DA = [
  "Januar", "Februar", "Marts", "April", "Maj", "Juni",
  "Juli", "August", "September", "Oktober", "November", "December",
];

const GROUP_ORDER = { O: 0, W: 1, K: 2 };
const GROUP_LABELS = { O: "Varme", W: "Varmt vand", K: "Koldt vand" };

// Optional `meter_type` card config -> which allocationUnit to show only.
// Unset/unrecognized -> null -> existing "all three side by side" behavior,
// unchanged.
const METER_TYPE_TO_ALLOCATION_UNIT = { heat: "O", hot_water: "W", cold_water: "K" };

const SUBTITLE_PARTS = {
  O: "Varme (varmefordelingsmåler, enheder)",
  W: "Varmt vand (m³)",
  K: "Koldt vand (m³)",
};
const SUBTITLE_ALL = Object.values(SUBTITLE_PARTS).join(" · ");

function formatConsumption(value, unit, fractionDigits) {
  if (value === null || value === undefined) return "—";
  // Heat ("enheder") is always a whole pulse count — never show decimals for
  // it, unlike water (m³) which keeps its usual 2 decimals by default.
  // Callers (e.g. the rolling 30-day summary, matched against Brunata's own
  // portal precision for easy cross-checking) can override this.
  if (fractionDigits === undefined) fractionDigits = unit === "enheder" ? 0 : 2;
  const formatted = value.toLocaleString("da-DK", {
    maximumFractionDigits: fractionDigits,
    minimumFractionDigits: fractionDigits,
  });
  // Heat's unit is already named once in the group's subtitle header —
  // repeating "enheder" on every single row/total value is redundant, so
  // just show the number. Water keeps its "m³" suffix on every value.
  return unit === "enheder" ? formatted : `${formatted} ${unit}`;
}

/** True only for the real, current calendar month (today's date) — not based
 * on whether data exists, so a genuine data gap in a past month is never
 * mistaken for "this month is still in progress", and vice versa.
 */
function isCurrentCalendarMonth(year, month) {
  const now = new Date();
  return year === now.getFullYear() && month === now.getMonth() + 1;
}

function formatYoy(yoyPercent) {
  if (yoyPercent === null || yoyPercent === undefined) {
    return { text: "—", color: null };
  }
  const sign = yoyPercent > 0 ? "+" : "";
  const text = `${sign}${yoyPercent.toFixed(0)}%`;
  // Confirmed with user: green = decrease (good), red = increase, on the
  // %% text only, not the bar — matches Brunata's own portal convention.
  const color = yoyPercent < 0 ? "var(--success-color, green)" : yoyPercent > 0 ? "var(--error-color, red)" : null;
  return { text, color };
}

/** Simple inline-SVG bar chart for one month's daily consumption. */
function buildDailyChart(days, unit) {
  const width = 320;
  const height = 140;
  const padding = { top: 14, right: 8, bottom: 20, left: 8 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const values = days
    .map((d) => d.consumption)
    .filter((v) => v !== null && v !== undefined);
  const maxValue = values.length ? Math.max(...values, 0) : 0;

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "brunata-daily-chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Dagligt forbrug i ${unit}`);

  const barSlot = chartWidth / days.length;
  const barWidth = Math.max(barSlot * 0.6, 1);

  days.forEach((day, i) => {
    const x = padding.left + i * barSlot + (barSlot - barWidth) / 2;

    if (day.consumption === null || day.consumption === undefined) {
      // Missing/reset day — a small dashed marker on the baseline instead
      // of a bar, so it reads as "no data" rather than "zero".
      const line = document.createElementNS(SVG_NS, "line");
      line.setAttribute("x1", x);
      line.setAttribute("x2", x + barWidth);
      line.setAttribute("y1", padding.top + chartHeight);
      line.setAttribute("y2", padding.top + chartHeight);
      line.setAttribute("class", "brunata-chart-missing");
      const titleEl = document.createElementNS(SVG_NS, "title");
      titleEl.textContent = `${day.day}. — ingen data`;
      line.appendChild(titleEl);
      svg.appendChild(line);
      return;
    }

    const barHeight = maxValue > 0 ? (day.consumption / maxValue) * chartHeight : 0;
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", x);
    rect.setAttribute("y", padding.top + chartHeight - barHeight);
    rect.setAttribute("width", barWidth);
    rect.setAttribute("height", Math.max(barHeight, 0.5));
    rect.setAttribute("class", "brunata-chart-bar");
    const titleEl = document.createElementNS(SVG_NS, "title");
    titleEl.textContent = `${day.day}. — ${formatConsumption(day.consumption, unit)}`;
    rect.appendChild(titleEl);
    svg.appendChild(rect);
  });

  // X-axis: day-of-month ticks (1, 5, 10, 15, ... plus the last day) — every
  // day would overlap for a 28-31 day month.
  days.forEach((day, i) => {
    const isLast = i === days.length - 1;
    if (day.day !== 1 && day.day % 5 !== 0 && !isLast) return;
    const x = padding.left + i * barSlot + barSlot / 2;
    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", x);
    label.setAttribute("y", height - 4);
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("class", "brunata-chart-axis-label");
    label.textContent = String(day.day);
    svg.appendChild(label);
  });

  // Y-axis: just the unit, top-left — this is a simple embedded chart, not
  // a full axis with tick values.
  const unitLabel = document.createElementNS(SVG_NS, "text");
  unitLabel.setAttribute("x", padding.left);
  unitLabel.setAttribute("y", padding.top - 4);
  unitLabel.setAttribute("class", "brunata-chart-axis-label");
  unitLabel.textContent = unit;
  svg.appendChild(unitLabel);

  return svg;
}

class BrunataMonthlyCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._activeDailyKey = null; // "meterId-year-month" of the currently shown daily chart, if any
      // meter_id (string) -> { monthly_summary, rolling_summary }, kept in
      // sync via _subscribeSummaries() below. Used to render the initial/
      // default view without any WS round-trip — see _loadRollingSummary
      // and _loadYear's `year === null` branch.
      this._summaries = {};
      this._render();
      this._subscribeSummaries();
      this._loadMeters();
    }
  }

  disconnectedCallback() {
    this._summariesUnsub?.();
  }

  async _subscribeSummaries() {
    // Reuses hass.connection — the same long-lived, auto-reconnecting WS
    // connection the frontend itself uses for entity-state sync — instead
    // of a one-shot hass.callWS(). This is what makes the dashboard robust
    // against a momentary WS hiccup at the exact moment it loads: a plain
    // RPC fired at the wrong instant simply fails, while a subscription
    // resumes automatically once the connection is back, and always
    // delivers its current snapshot immediately upon (re)subscribing.
    // Fails silently (falls back to on-demand WS calls per meter below) if
    // the backend doesn't support this command yet (e.g. mid-upgrade).
    try {
      this._summariesUnsub = await this._hass.connection.subscribeMessage(
        (snapshot) => {
          this._summaries = snapshot || {};
        },
        { type: "brunata/subscribe_summaries" }
      );
    } catch (err) {
      console.warn("brunata-monthly-card: kunne ikke abonnere på forudberegnede resuméer", err);
    }
  }

  async _loadMeters() {
    let meters = await this._hass.callWS({ type: "brunata/list_meters" });
    if (this._filterAllocationUnit) {
      // meter_type is set — show only this one type. There can still be
      // more than one meter of that type (Del 3a): all of them are shown,
      // just none of the other two types.
      meters = meters.filter((m) => m.allocation_unit === this._filterAllocationUnit);
    }
    this._meters = meters;
    this._root.querySelector(".brunata-loading")?.remove();

    const groups = {};
    for (const meter of meters) {
      (groups[meter.allocation_unit] ||= []).push(meter);
    }

    const container = this._root.querySelector(".brunata-groups");
    const rollingContainer = this._root.querySelector(".brunata-rolling-groups");
    // Skip the 3-group grid layout entirely when filtered to one type, so
    // that one group fills the card's full width instead. Both grids share
    // the same column layout, so they're toggled/cleared identically.
    const isSingle = Boolean(this._filterAllocationUnit);
    container.classList.toggle("brunata-groups--single", isSingle);
    rollingContainer.classList.toggle("brunata-groups--single", isSingle);
    container.innerHTML = "";
    rollingContainer.innerHTML = "";

    const unitTypes = Object.keys(groups).sort(
      (a, b) => (GROUP_ORDER[a] ?? 99) - (GROUP_ORDER[b] ?? 99)
    );

    // Build all column skeletons (DOM structure + year-dropdown wiring)
    // synchronously first, so the layout appears immediately — then load
    // each meter's actual data ONE AT A TIME below, rather than firing all
    // monthly_summary calls at once. Serialized deliberately: performance is
    // irrelevant for a handful of quick metadata calls, and this removes any
    // possibility of concurrent calls interfering with each other at the
    // source, instead of chasing a hard-to-reproduce race condition.
    const pendingLoads = [];
    for (const unitType of unitTypes) {
      const groupEl = document.createElement("div");
      groupEl.className = "brunata-group";
      const heading = document.createElement("h3");
      heading.textContent = GROUP_LABELS[unitType] || unitType;
      groupEl.appendChild(heading);

      // Mirrors groupEl's position (same "brunata-group" grid-column rules
      // apply to both grids) but holds only the rolling-summary boxes, one
      // per meter in this group, stacked in the same order as the meter
      // columns below.
      const rollingGroupEl = document.createElement("div");
      rollingGroupEl.className = "brunata-group";

      for (const meter of groups[unitType]) {
        const { columnEl, list, yearSelect } = this._buildMeterColumnSkeleton(meter);
        groupEl.appendChild(columnEl);

        const rollingEl = this._buildRollingSummarySkeleton();
        rollingGroupEl.appendChild(rollingEl);

        pendingLoads.push({ meter, list, yearSelect, rollingEl });
      }
      container.appendChild(groupEl);
      rollingContainer.appendChild(rollingGroupEl);
    }

    for (const { meter, list, yearSelect, rollingEl } of pendingLoads) {
      // Same deliberate serialization as the year loads below — one WS call
      // at a time, in a fixed order, rather than firing every meter's
      // requests concurrently.
      await this._loadRollingSummary(meter, rollingEl);
      await this._loadYear(meter, list, yearSelect, null);
    }
  }

  _buildMeterColumnSkeleton(meter) {
    const columnEl = document.createElement("div");
    columnEl.className = "brunata-meter-column";

    const header = document.createElement("div");
    header.className = "brunata-meter-header";

    const label = document.createElement("span");
    label.className = "brunata-meter-label";
    label.textContent = meter.name;
    header.appendChild(label);

    const yearSelect = document.createElement("select");
    yearSelect.className = "brunata-year-select";
    yearSelect.hidden = true; // shown once we know which years are available
    header.appendChild(yearSelect);

    columnEl.appendChild(header);

    const list = document.createElement("div");
    list.className = "brunata-month-list";
    list.textContent = "Indlæser…";
    columnEl.appendChild(list);

    // Year changes are individual, user-triggered, one-at-a-time actions —
    // no serialization needed here, only for the initial burst of loads.
    yearSelect.addEventListener("change", () =>
      this._loadYear(meter, list, yearSelect, parseInt(yearSelect.value, 10))
    );

    return { columnEl, list, yearSelect };
  }

  /** "Forbrug sidste 30 dage" summary box — mirrors Brunata's own portal's
   * rolling-window cards. Deliberately its own top-level row above the
   * meter tables (not nested inside each meter's own column) so it reads as
   * a distinct summary section, matching Brunata's own layout.
   */
  _buildRollingSummarySkeleton() {
    const rollingEl = document.createElement("div");
    rollingEl.className = "brunata-rolling-summary";
    rollingEl.textContent = "Indlæser…";
    return rollingEl;
  }

  async _loadRollingSummary(meter, rollingEl) {
    // Prefer the precomputed value pushed via _subscribeSummaries — only
    // falls through to the on-demand WS call if that hasn't arrived yet
    // (e.g. right after a reload, before the coordinator's first poll) or
    // the backend doesn't support the subscription at all.
    const precomputed = this._summaries[String(meter.meter_id)]?.rolling_summary;
    const summary = precomputed || (await this._hass.callWS({
      type: "brunata/rolling_summary",
      meter_id: meter.meter_id,
    }));

    const isHeat = meter.allocation_unit === "O" && summary.scale;
    const unit = isHeat ? "enheder" : this._unitFor(meter.entity_id);
    const toDisplay = (value) =>
      value === null || value === undefined ? null : isHeat ? value / summary.scale : value;

    rollingEl.textContent = "";

    // Label and value share one row (label left, value right) — matching
    // the table rows/Total row below, and Brunata's own portal layout.
    const mainRow = document.createElement("div");
    mainRow.className = "brunata-rolling-main";

    const label = document.createElement("span");
    label.className = "brunata-rolling-label";
    label.textContent = "Forbrug sidste 30 dage";
    mainRow.appendChild(label);

    // 3 decimals for water, matching Brunata's own portal's "Sidste 30
    // dage" cards precision exactly (e.g. "1,958 m³") — deliberately more
    // precise than the 2-decimal monthly table, since this box's whole
    // purpose is letting the user cross-check our number against theirs.
    const fractionDigits = unit === "enheder" ? 0 : 3;

    const valueEl = document.createElement("span");
    valueEl.className = "brunata-rolling-value";
    valueEl.textContent = formatConsumption(toDisplay(summary.total), unit, fractionDigits);
    mainRow.appendChild(valueEl);

    rollingEl.appendChild(mainRow);

    const diff = toDisplay(summary.diff_from_last_year);
    if (diff !== null) {
      const diffEl = document.createElement("div");
      diffEl.className = "brunata-rolling-diff";
      const arrow = diff > 0 ? "↗" : diff < 0 ? "↘" : "→";
      const sign = diff > 0 ? "+" : "";
      diffEl.textContent =
        `${arrow} ${sign}${formatConsumption(diff, unit, fractionDigits)} ift. samme periode sidste år`;
      rollingEl.appendChild(diffEl);
    }
  }

  async _loadYear(meter, listEl, yearSelect, year) {
    listEl.textContent = "Indlæser…";
    // The precomputed cache only ever holds the default/most-recent year
    // (same as calling brunata/monthly_summary with no `year`) — switching
    // to an explicit other year always goes through the on-demand WS call,
    // same as before.
    const precomputed = year === null
      ? this._summaries[String(meter.meter_id)]?.monthly_summary
      : null;
    let summary = precomputed;
    if (!summary) {
      const msg = { type: "brunata/monthly_summary", meter_id: meter.meter_id };
      if (year !== null) msg.year = year;
      summary = await this._hass.callWS(msg);
    }

    if (!summary.available_years.length) {
      listEl.textContent = "Ingen data endnu.";
      yearSelect.hidden = true;
      return;
    }

    if (yearSelect.options.length === 0) {
      // Newest year first in the dropdown.
      for (const y of [...summary.available_years].reverse()) {
        const option = document.createElement("option");
        option.value = String(y);
        option.textContent = String(y);
        yearSelect.appendChild(option);
      }
    }
    yearSelect.value = String(summary.year);
    yearSelect.hidden = false;

    this._renderMonths(listEl, meter, summary);
  }

  _renderMonths(listEl, meter, summary) {
    listEl.textContent = "";

    // Heat (allocation_unit "O") shows raw "enheder" in this table only —
    // the sensor state, Energy Dashboard, and the imported statistics all
    // stay in kWh (statistics.py). `summary.scale` is the same meterValue*scale
    // factor already cached per meter_id and applied when the kWh statistics
    // were built, so dividing back by it recovers the original raw reading.
    const isHeat = meter.allocation_unit === "O" && summary.scale;
    const unit = isHeat ? "enheder" : this._unitFor(meter.entity_id);
    const toDisplay = (value) =>
      value === null || value === undefined ? null : isHeat ? value / summary.scale : value;

    // Ascending calendar order — January first, December last — regardless
    // of which year is selected or whether a given month has data yet.
    // summary.months from the backend is already Jan-Dec ordered
    // (compute_monthly_summary_for_year iterates range(1, 13)); no reversal.
    for (const row of summary.months) {
      const rowEl = document.createElement("div");
      rowEl.className = "brunata-month-row";

      const nameEl = document.createElement("span");
      nameEl.className = "brunata-month-name";
      nameEl.textContent = MONTH_NAMES_DA[row.month - 1];

      const valueEl = document.createElement("span");
      valueEl.className = "brunata-month-value";

      // Distinguish "this calendar month, still in progress" from a genuine
      // data gap in a past month — both currently show consumption=null
      // (the reset-guard in aggregation.py can't tell "meter reset" from "no
      // next-period row yet" from sum alone), but they mean very different
      // things to the user. The reset-guard logic itself is untouched — this
      // only changes how a null result is PRESENTED for the current month.
      const isOngoing = row.consumption === null && isCurrentCalendarMonth(summary.year, row.month);
      if (isOngoing) {
        valueEl.textContent = "— ";
        const ongoingLabel = document.createElement("span");
        ongoingLabel.className = "brunata-month-ongoing-label";
        ongoingLabel.textContent = "(i gang)";
        ongoingLabel.title = "Denne måned er ikke afsluttet endnu — ikke manglende data";
        valueEl.appendChild(ongoingLabel);
      } else {
        valueEl.textContent = formatConsumption(toDisplay(row.consumption), unit);
      }

      const yoy = formatYoy(row.yoy_percent);
      const yoyEl = document.createElement("span");
      yoyEl.className = "brunata-month-yoy";
      yoyEl.textContent = yoy.text;
      if (yoy.color) yoyEl.style.color = yoy.color;

      rowEl.append(nameEl, valueEl, yoyEl);

      if (row.consumption !== null) {
        rowEl.addEventListener("click", () =>
          this._toggleDaily(meter, summary.year, row, rowEl, unit, isHeat ? summary.scale : null)
        );
      } else if (isOngoing) {
        rowEl.classList.add("brunata-month-row-ongoing");
      } else {
        rowEl.classList.add("brunata-month-row-disabled");
      }

      listEl.appendChild(rowEl);
    }

    const totalEl = document.createElement("div");
    totalEl.className = "brunata-total-row";
    const totalLabel = document.createElement("span");
    totalLabel.textContent = "Total";
    const totalValue = document.createElement("span");
    totalValue.className = "brunata-total-value";
    totalValue.textContent = formatConsumption(toDisplay(summary.total_consumption), unit);
    totalEl.append(totalLabel, totalValue);
    listEl.appendChild(totalEl);
  }

  async _toggleDaily(meter, year, row, rowEl, unit, scale) {
    const key = `${meter.meter_id}-${year}-${row.month}`;
    const panel = this._root.querySelector(".brunata-detail-panel");
    const headingEl = panel.querySelector(".brunata-detail-heading");
    const contentEl = panel.querySelector(".brunata-detail-content");

    // Only one row highlighted/expanded at a time, regardless of which
    // meter column it's in.
    this._root
      .querySelectorAll(".brunata-month-row.active")
      .forEach((el) => el.classList.remove("active"));

    if (this._activeDailyKey === key) {
      // Clicking the same month again closes the panel.
      this._activeDailyKey = null;
      panel.hidden = true;
      contentEl.textContent = "";
      return;
    }

    this._activeDailyKey = key;
    rowEl.classList.add("active");
    panel.hidden = false;

    // Single, clear heading for the whole chart: which meter/month, and the
    // value type + unit ("Forbrug (enheder)" / "Forbrug (m³)") — shown once
    // here, not repeated per bar/tooltip inside the chart itself.
    headingEl.textContent = "";
    const meterNameEl = document.createElement("div");
    meterNameEl.className = "brunata-detail-meter";
    meterNameEl.textContent = meter.name;
    const titleEl = document.createElement("div");
    titleEl.className = "brunata-detail-title";
    titleEl.textContent = `${MONTH_NAMES_DA[row.month - 1]} ${year} — Forbrug (${unit})`;
    headingEl.append(meterNameEl, titleEl);

    contentEl.textContent = "Indlæser…";

    const days = await this._hass.callWS({
      type: "brunata/daily_breakdown",
      meter_id: meter.meter_id,
      year,
      month: row.month,
    });

    if (this._activeDailyKey !== key) return; // a different month was clicked meanwhile

    const displayDays = days.map((d) => ({
      day: d.day,
      consumption:
        d.consumption === null || d.consumption === undefined
          ? null
          : scale
          ? d.consumption / scale
          : d.consumption,
    }));

    contentEl.textContent = "";
    contentEl.appendChild(buildDailyChart(displayDays, unit));
  }

  _unitFor(entityId) {
    return this._hass.states[entityId]?.attributes?.unit_of_measurement || "";
  }

  _render() {
    // meter_type: "heat" | "hot_water" | "cold_water" (optional). Unset or
    // unrecognized -> null -> unchanged "all three side by side" behavior.
    this._filterAllocationUnit = METER_TYPE_TO_ALLOCATION_UNIT[this._config.meter_type] || null;
    // show_title: only relevant when meter_type is set and a separate
    // Lovelace heading card provides the "Forbrug" title instead. Defaults
    // to true (existing ha-card header shown) for backward compatibility.
    const showTitle = this._config.show_title !== false;
    const subtitleText = this._filterAllocationUnit
      ? SUBTITLE_PARTS[this._filterAllocationUnit]
      : SUBTITLE_ALL;

    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px; width: 100%; max-width: 1400px; margin: 0 auto; box-sizing: border-box; }
        .brunata-subtitle {
          font-size: 0.9em; opacity: 0.7; margin: -8px 0 12px 0;
        }
        /* Main table on the left, daily-chart detail panel on the right —
           side by side when there's room, stacked (panel below the table)
           when the card is too narrow (e.g. meter_type in a single Lovelace
           section) rather than overflowing/squeezing into the table. */
        .brunata-layout { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
        /* Always side by side in one row (Varme / Varmt vand / Koldt vand),
           with the gap between them scaling with column width instead of a
           fixed px/rem number: 5 equal 1fr columns, groups placed in 1/3/5,
           so the empty 2/4 columns are always exactly one column wide —
           i.e. the same width as a content column, at any card size. No
           separate gap property needed (or wanted — it would add on top of
           the column-based spacing). */
        .brunata-groups {
          flex: 1 1 240px; min-width: 0;
          display: grid;
          grid-template-columns: 1fr 1fr 1fr 1fr 1fr;
        }
        /* meter_type set — only one group, no 3-column grid needed; let it
           fill the card's full width instead (grid-column below is simply
           ignored once display isn't grid). */
        .brunata-groups.brunata-groups--single { display: block; }
        .brunata-group:nth-child(1) { grid-column: 1; }
        .brunata-group:nth-child(2) { grid-column: 3; }
        .brunata-group:nth-child(3) { grid-column: 5; }
        /* "Forbrug sidste 30 dage" boxes — a separate row above the meter
           tables (not nested inside each meter's own column), mirroring
           Brunata's own portal layout. Shares .brunata-groups' exact column
           grid/placement rules above so a box always lines up with the
           table column it belongs to. */
        .brunata-rolling-groups {
          display: grid;
          grid-template-columns: 1fr 1fr 1fr 1fr 1fr;
          margin-bottom: 20px;
        }
        .brunata-rolling-groups.brunata-groups--single { display: block; }
        .brunata-detail-panel {
          /* flex-grow/shrink both allowed (unlike the old fixed 0 0 360px):
             shares space with the table when there's room, and — combined
             with .brunata-layout's flex-wrap above — drops to its own full-
             width row below the table instead of being forced to squeeze
             into a sliver next to it when the card is narrow. */
          flex: 1 1 300px; min-width: 300px; max-width: 420px;
          border-left: 1px solid var(--divider-color); padding-left: 16px;
        }
        .brunata-detail-heading { margin-bottom: 8px; }
        .brunata-detail-meter { font-size: 0.85em; opacity: 0.7; }
        .brunata-detail-title { font-weight: 600; }
        .brunata-group h3 { margin: 0 0 8px 0; }
        .brunata-meter-column {
          margin-bottom: 16px;
          border: 1px solid var(--divider-color); border-radius: 8px; padding: 12px;
        }
        .brunata-meter-header {
          display: flex; align-items: center; justify-content: space-between;
          gap: 8px; margin-bottom: 4px;
        }
        .brunata-meter-label { font-weight: 500; opacity: 0.8; }
        .brunata-rolling-summary {
          background: var(--card-background-color);
          border: 1px solid var(--divider-color); border-radius: 8px;
          padding: 10px 14px; margin-bottom: 8px;
        }
        .brunata-rolling-main {
          display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
        }
        .brunata-rolling-label { font-weight: 500; }
        .brunata-rolling-value { font-size: 1.2em; font-weight: 600; white-space: nowrap; }
        .brunata-rolling-diff { font-size: 0.8em; opacity: 0.7; margin-top: 4px; }
        .brunata-year-select {
          font: inherit; color: inherit; background: var(--card-background-color);
          border: 1px solid var(--divider-color); border-radius: 4px; padding: 2px 4px;
        }
        .brunata-month-row {
          display: flex; gap: 12px; justify-content: space-between;
          padding: 4px 0; cursor: pointer;
        }
        .brunata-month-row:hover { background: var(--secondary-background-color); }
        .brunata-month-row.active { background: var(--secondary-background-color); font-weight: 600; }
        .brunata-month-row-disabled { cursor: default; opacity: 0.6; }
        /* Deliberately distinct from .brunata-month-row-disabled above (which
           is for genuine data gaps) — the current month isn't "missing" data,
           it just isn't finished yet. Italic + accent color instead of the
           plain dimmed/greyed treatment used for real gaps. */
        .brunata-month-row-ongoing { cursor: default; }
        .brunata-month-row-ongoing .brunata-month-value { font-style: italic; }
        .brunata-month-ongoing-label {
          font-style: italic; opacity: 0.8; margin-left: 4px;
          color: var(--info-color, #039be5);
        }
        .brunata-month-name { min-width: 6em; }
        .brunata-month-value { text-align: right; white-space: nowrap; }
        .brunata-month-yoy { min-width: 3.5em; text-align: right; white-space: nowrap; }
        .brunata-total-value { white-space: nowrap; }
        .brunata-daily-chart { width: 100%; height: auto; display: block; }
        .brunata-chart-bar { fill: var(--primary-color, #03a9f4); }
        .brunata-chart-missing {
          stroke: var(--disabled-text-color, #999); stroke-width: 2; stroke-dasharray: 2,2;
        }
        .brunata-chart-axis-label { font-size: 7px; fill: var(--secondary-text-color); }
        .brunata-total-row {
          display: flex; justify-content: space-between; gap: 12px;
          margin-top: 4px; padding-top: 6px;
          border-top: 1px solid var(--divider-color); font-weight: 600;
        }
        .brunata-loading { opacity: 0.7; }
      </style>
      <ha-card ${showTitle ? 'header="Forbrug"' : ""}>
        <div class="brunata-subtitle">${subtitleText}</div>
        <div class="brunata-loading">Indlæser målere…</div>
        <div class="brunata-rolling-groups"></div>
        <div class="brunata-layout">
          <div class="brunata-groups"></div>
          <div class="brunata-detail-panel" hidden>
            <div class="brunata-detail-heading"></div>
            <div class="brunata-detail-content"></div>
          </div>
        </div>
      </ha-card>
    `;
  }
}

customElements.define("brunata-monthly-card", BrunataMonthlyCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "brunata-monthly-card",
  name: "Brunata forbrug",
  description: "Årligt forbrug pr. måler (måned for måned) med år-til-år-sammenligning.",
});

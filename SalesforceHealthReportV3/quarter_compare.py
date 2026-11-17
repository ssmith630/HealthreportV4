"""Two-quarter pipeline comparison (current and next quarter) for the HTML dashboard.

Pure functions: `summarize` turns opportunity rows into per-quarter metrics, `render` turns the
two summaries into a self-contained HTML section. The section needs no scripts: the
"Side by side" / "Tabbed" switch and the quarter tabs are CSS-only (radio inputs).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape

from quarters import ROLES, Window

# role -> (css class, glyph entity). The glyph and the word both name the quarter, so colour is never the only cue.
ROLE_STYLE = {"Current": ("cur", "&#9679;"), "Next": ("next", "&#9654;")}
TOP_N = 5
ACCOUNT_ROWS = 12

CSS = """
.mq-r{position:absolute;opacity:0;pointer-events:none}
.mq-legend{display:flex;flex-wrap:wrap;gap:.5rem 1.25rem;font-size:.8125rem;color:var(--muted);margin-bottom:1rem}
.mq-chip{display:inline-block;padding:.05rem .55rem;font-size:.75rem;font-weight:600;color:#fff}
.mq-chip.cur,.mq-bar.cur i{background:#0f62fe}.mq-chip.next,.mq-bar.next i{background:#007d79}
.mq-ctl{display:flex;flex-wrap:wrap;gap:.75rem 2rem;align-items:center;margin-bottom:1rem}
.mq-ctl label{cursor:pointer;padding:.45rem .9rem;border:1px solid #c6c6c6;background:#fff;font-size:.8125rem}
.mq-ctl label:hover{background:#e8e8e8}
.mq-ctl .lbl{font-size:.75rem;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:.5px;margin-right:.5rem}
.mq-r:focus-visible ~ .mq-ctl{outline:2px solid var(--blue);outline-offset:2px}
#mq-side:checked ~ .mq-ctl label[for=mq-side],#mq-tab:checked ~ .mq-ctl label[for=mq-tab]{background:#161616;color:#fff;border-color:#161616}
#mq-side:checked ~ .mq-ctl .mq-tabs{display:none}
#mq-t0:checked ~ .mq-ctl label[for=mq-t0]{border-bottom:4px solid #0f62fe;font-weight:600}
#mq-t1:checked ~ .mq-ctl label[for=mq-t1]{border-bottom:4px solid #007d79;font-weight:600}
.mq-cols{display:grid;gap:1rem;margin-bottom:1.5rem}
#mq-side:checked ~ .mq-cols{grid-template-columns:repeat(2,minmax(0,1fr))}
#mq-tab:checked ~ .mq-cols{grid-template-columns:minmax(0,1fr)}
#mq-tab:checked ~ .mq-cols .mq-col{display:none}
#mq-tab:checked ~ #mq-t0:checked ~ .mq-cols .mq-col.cur,
#mq-tab:checked ~ #mq-t1:checked ~ .mq-cols .mq-col.next{display:block}
@media(max-width:900px){#mq-side:checked ~ .mq-cols{grid-template-columns:minmax(0,1fr)}}
.mq-col{background:#fafafa;border:1px solid var(--line);border-top:4px solid var(--c);padding:1rem}
.mq-col.cur{--c:#0f62fe}.mq-col.next{--c:#007d79}
.mq-col h3{font-size:1.5rem;font-weight:400;line-height:1.2}
.mq-col h4{font-size:.75rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin:1rem 0 .4rem}
.mq-role{font-size:.75rem;font-weight:600;color:var(--c);text-transform:uppercase;letter-spacing:.5px}
.mq-stats{display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.75rem}
.mq-stat{background:#fff;border:1px solid var(--line);padding:.5rem .6rem}
.mq-stat .l{font-size:.6875rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.mq-stat .v{font-size:1.25rem;font-weight:300}
.mq-stage{display:grid;grid-template-columns:minmax(0,7rem) 1fr auto;gap:.5rem;align-items:center;font-size:.8125rem;margin:.2rem 0}
.mq-stage span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mq-bar{background:#e0e0e0;height:.6rem;display:block}.mq-bar i{display:block;height:100%}
.mq-stage .mq-bar i{background:var(--c)}
.mq-top{list-style:none;font-size:.8125rem}
.mq-top li{padding:.4rem 0;border-bottom:1px solid var(--line)}
.mq-top b{font-weight:600}.mq-top small{display:block;color:var(--muted)}
.mq-trend{display:grid;grid-template-columns:7rem 1fr auto;gap:.5rem;align-items:center;font-size:.8125rem;margin:.3rem 0}
.mq-trend .mq-bar{height:.9rem}
.mq-trend .mq-bar.sub{height:.45rem;opacity:.45;margin-top:2px}
.up{color:#0e6027;font-weight:600}.down{color:#a2191f;font-weight:600}.flat{color:var(--muted);font-weight:600}
.mq-ins{margin:0 0 1.5rem 1.25rem;font-size:.875rem}.mq-ins li{margin:.3rem 0}
.mq-sub{font-size:1rem;font-weight:400;margin:1.25rem 0 .5rem}
"""


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(v: float) -> str:
    return f"${v:,.0f}"


def _short(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:,.0f}"


def _in(o: dict, w: Window) -> bool:
    try:
        return w.contains(date.fromisoformat(str(o.get("close_date"))[:10]))
    except ValueError:
        return False


def summarize(opps: list[dict], w: Window) -> dict:
    """Metrics for the opportunities closing inside window `w`."""
    rows = [o for o in opps if _in(o, w)]
    amount = sum(_num(o.get("amount")) for o in rows)
    expected = sum(_num(o.get("expected_revenue")) for o in rows)
    commit = sum(_num(o.get("amount")) for o in rows if "commit" in str(o.get("forecast_pipeline") or "").lower())
    accounts: dict[str, float] = defaultdict(float)
    stages: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for o in rows:
        accounts[o.get("account") or ""] += _num(o.get("amount"))
        s = stages[o.get("stage") or "(no stage)"]
        s[0] += 1
        s[1] += _num(o.get("amount"))
    return {
        "window": w, "rows": rows, "count": len(rows), "amount": amount, "expected": expected, "commit": commit,
        "avg": amount / len(rows) if rows else 0.0, "confidence": expected / amount if amount else 0.0,
        "accounts": dict(accounts), "stages": dict(stages),
        "top": sorted(rows, key=lambda o: -_num(o.get("amount")))[:TOP_N],
    }


def _delta(a: float, b: float) -> tuple[str, str]:
    """Change from a to b as (css class, text with arrow)."""
    if a == b:
        return "flat", "&#9632; No change"
    if not a:
        return "up", "&#9650; New"
    pct = (b - a) / a * 100
    if abs(pct) < 1:
        return "flat", "&#9632; Flat"
    return ("up", f"&#9650; {pct:+.0f}%") if pct > 0 else ("down", f"&#9660; {pct:+.0f}%")


def _words(a: float, b: float) -> str:
    """Plain-text change from a to b."""
    if a == b:
        return "no change"
    if not a:
        return "new pipeline"
    pct = (b - a) / a * 100
    if abs(pct) < 1:
        return "flat"
    return f"{'up' if pct > 0 else 'down'} {abs(pct):.0f}%"


def _delta_cell(a: float, b: float) -> str:
    cls, text = _delta(a, b)
    return f'<td class="num {cls}">{text}</td>'


def _account_status(c: float, n: float) -> tuple[str, str]:
    if n and not c:
        return "up", "Emerging"
    if c and not n:
        return "down", "Drops off"
    if not c and not n:
        return "flat", "None"
    if n > c * 1.05:
        return "up", "Growing"
    if n < c * 0.95:
        return "down", "Declining"
    return "flat", "Steady"


def _insights(s: list[dict]) -> list[str]:
    c, n = s
    out = []
    out.append(f"Open pipeline runs {_money(c['amount'])} in {c['window'].label} and {_money(n['amount'])} in "
               f"{n['window'].label}. Current to next: {_words(c['amount'], n['amount'])}.")
    if c["count"] != n["count"]:
        out.append(f"Deal count changes from {c['count']} to {n['count']} between {c['window'].label} and "
                   f"{n['window'].label}; average deal size moves from {_money(c['avg'])} to {_money(n['avg'])}.")
    emerging = sorted(((a, v) for a, v in n["accounts"].items() if v and not c["accounts"].get(a)),
                      key=lambda x: -x[1])
    if emerging:
        top = ", ".join(f"{a} ({_short(v)})" for a, v in emerging[:3])
        out.append(f"{len(emerging)} accounts have {n['window'].label} pipeline but none in {c['window'].label}"
                   f" ({_short(sum(v for _, v in emerging))} in total). Largest: {top}.")
    dropping = sorted(((a, v) for a, v in c["accounts"].items() if v and not n["accounts"].get(a)),
                      key=lambda x: -x[1])
    if dropping:
        top = ", ".join(f"{a} ({_short(v)})" for a, v in dropping[:3])
        out.append(f"{len(dropping)} accounts have {c['window'].label} pipeline but none yet in "
                   f"{n['window'].label}. Largest: {top}.")
    for q in (c, n):
        if q["amount"] and q["accounts"]:
            name, v = max(q["accounts"].items(), key=lambda x: x[1])
            if v / q["amount"] >= 0.4:
                out.append(f"Concentration risk: {name} is {v / q['amount']:.0%} of {q['window'].label} pipeline.")
    if c["amount"] and n["amount"] and abs(n["confidence"] - c["confidence"]) >= 0.05:
        out.append(f"Expected revenue is {c['confidence']:.0%} of pipeline in {c['window'].label} and "
                   f"{n['confidence']:.0%} in {n['window'].label}.")
    return out


def _link(name: str, urls: dict[str, str]) -> str:
    url = urls.get(name, "")
    if url.startswith("https://"):
        return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">{_e(name)}</a>'
    return _e(name)


def _opp_link(o: dict) -> str:
    url = o.get("record_url") or ""
    if url.startswith("https://"):
        return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">{_e(o.get("name"))}</a>'
    return _e(o.get("name"))


def _span(w: Window) -> str:
    return f"{w.start:%b} {w.start.day} &ndash; {w.end:%b} {w.end.day}, {w.end.year}"


def _column(s: dict, role: str, urls: dict[str, str]) -> str:
    cls, glyph = ROLE_STYLE[role]
    w = s["window"]
    stats = "".join(f'<div class="mq-stat"><div class="l">{l}</div><div class="v">{v}</div></div>' for l, v in [
        ("Open opps", s["count"]), ("Pipeline", _short(s["amount"])), ("Expected rev.", _short(s["expected"])),
        ("Avg. deal", _short(s["avg"])), ("Commit", _short(s["commit"])), ("Accounts", len(s["accounts"]))])
    stage_rows = "".join(
        f'<div class="mq-stage"><span title="{_e(name)}">{_e(name)}</span>'
        f'<span class="mq-bar"><i style="width:{(v[1] / s["amount"] * 100 if s["amount"] else 0):.0f}%"></i></span>'
        f'<span>{v[0]} &middot; {_short(v[1])}</span></div>'
        for name, v in sorted(s["stages"].items(), key=lambda x: -x[1][1])) or '<p class="empty">No opportunities.</p>'
    top = "".join(
        f"<li><b>{_short(_num(o.get('amount')))}</b> {_opp_link(o)}"
        f"<small>{_link(o.get('account') or '', urls)} &middot; {_e(o.get('stage'))} &middot; closes {_e(o.get('close_date'))}</small></li>"
        for o in s["top"]) or '<li class="empty">No opportunities.</li>'
    return (f'<article class="mq-col {cls}" aria-label="{role} quarter, {_e(w.label)}">'
            f'<span class="mq-role"><span aria-hidden="true">{glyph}</span> {role} quarter</span>'
            f'<h3>{_e(w.label)}</h3><p class="sub">{_span(w)}</p><div class="mq-stats">{stats}</div>'
            f'<h4>Stage mix</h4>{stage_rows}<h4>Top {TOP_N} opportunities</h4><ul class="mq-top">{top}</ul></article>')


def _comparison_table(s: list[dict]) -> str:
    c, n = s
    metrics = [("Open opportunities", "count", str), ("Open pipeline", "amount", _money),
               ("Expected revenue", "expected", _money), ("Average deal size", "avg", _money),
               ("Commit pipeline", "commit", _money), ("Expected revenue / pipeline", "confidence", lambda v: f"{v:.0%}")]
    rows = [("Accounts with pipeline", len(c["accounts"]), len(n["accounts"]), str)]
    body = "".join(
        f'<tr><td>{label}</td><td class="num">{fmt(c[k])}</td><td class="num">{fmt(n[k])}</td>'
        f"{_delta_cell(c[k], n[k])}</tr>" for label, k, fmt in metrics)
    body += "".join(
        f'<tr><td>{label}</td><td class="num">{fmt(a)}</td><td class="num">{fmt(b)}</td>'
        f"{_delta_cell(a, b)}</tr>" for label, a, b, fmt in rows)
    head = "".join(f"<th>{h}</th>" for h in (
        "Metric", _e(c["window"].label), _e(n["window"].label), "Current &rarr; next"))
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _trend_bars(s: list[dict]) -> str:
    top = max((q["amount"] for q in s), default=0) or 1
    out = []
    for q, role in zip(s, ROLES):
        cls, glyph = ROLE_STYLE[role]
        out.append(
            f'<div class="mq-trend"><span><span aria-hidden="true">{glyph}</span> {_e(q["window"].label)}</span>'
            f'<span><span class="mq-bar {cls}"><i style="width:{q["amount"] / top * 100:.0f}%"></i></span>'
            f'<span class="mq-bar sub {cls}"><i style="width:{q["expected"] / top * 100:.0f}%"></i></span></span>'
            f'<span>{_money(q["amount"])}</span></div>')
    return "".join(out) + '<p class="hint">Dark bar: open pipeline. Light bar: expected revenue.</p>'


def _account_table(s: list[dict], urls: dict[str, str]) -> str:
    c, n = s
    names = set(c["accounts"]) | set(n["accounts"])
    rows = sorted(((a, c["accounts"].get(a, 0.0), n["accounts"].get(a, 0.0))
                   for a in names), key=lambda r: -(r[1] + r[2]))[:ACCOUNT_ROWS]
    body = "".join(
        f'<tr><td>{_link(a, urls)}</td><td class="num">{_money(y)}</td><td class="num">{_money(z)}</td>'
        f'<td class="{_account_status(y, z)[0]}">{_account_status(y, z)[1]}</td></tr>'
        for a, y, z in rows)
    head = "".join(f"<th>{h}</th>" for h in ("Account", _e(c["window"].label), _e(n["window"].label), "Trend"))
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(opps: list[dict], windows: list[Window], urls: dict[str, str] | None = None) -> str:
    """The comparison section. `windows` must be [current, next]."""
    urls = urls or {}
    s = [summarize(opps, w) for w in windows]
    title = f"Two-quarter pipeline comparison ({' / '.join(w.label for w in windows)})"
    if not any(q["count"] for q in s):
        return (f'<section class="panel"><h2>{_e(title)}</h2><p class="empty">No open opportunities close in these '
                "quarters. See Notes below.</p></section>")
    legend = "".join(f'<span><span class="mq-chip {ROLE_STYLE[r][0]}"><span aria-hidden="true">{ROLE_STYLE[r][1]}</span> '
                     f'{r}</span> {_e(q["window"].label)}</span>' for r, q in zip(ROLES, s))
    legend += ('<span><span class="up">&#9650; up</span> <span class="down">&#9660; down</span> '
               '<span class="flat">&#9632; flat</span> change between quarters</span>')
    tabs = "".join(f'<label for="mq-t{i}"><span aria-hidden="true">{ROLE_STYLE[r][1]}</span> {_e(q["window"].label)}</label>'
                   for i, (r, q) in enumerate(zip(ROLES, s)))
    cols = "".join(_column(q, r, urls) for r, q in zip(ROLES, s))
    insights = "".join(f"<li>{_e(t)}</li>" for t in _insights(s))
    return f"""<section class="panel" id="mq"><h2>{_e(title)}</h2>
<p class="hint">Open opportunities by close date, for the current and next quarter. Opportunities that closed won or
lost are not included.</p>
<div class="mq-legend">{legend}</div>
<input type="radio" class="mq-r" name="mqmode" id="mq-side" checked aria-label="Side by side view">
<input type="radio" class="mq-r" name="mqmode" id="mq-tab" aria-label="Tabbed view">
<input type="radio" class="mq-r" name="mqtab" id="mq-t0" checked aria-label="Show {_e(s[0]['window'].label)}">
<input type="radio" class="mq-r" name="mqtab" id="mq-t1" aria-label="Show {_e(s[1]['window'].label)}">
<div class="mq-ctl"><span><span class="lbl">View</span><label for="mq-side">Side by side</label><label for="mq-tab">Tabbed</label></span>
<span class="mq-tabs"><span class="lbl">Quarter</span>{tabs}</span></div>
<div class="mq-cols">{cols}</div>
<h3 class="mq-sub">Trend</h3>{_trend_bars(s)}
{_comparison_table(s)}
<h3 class="mq-sub">What stands out</h3><ul class="mq-ins">{insights}</ul>
<h3 class="mq-sub">Account movement (top {ACCOUNT_ROWS} by pipeline)</h3>{_account_table(s, urls)}
</section>"""

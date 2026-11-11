"""Regression tests for the 2026-11 UI overhaul (Midnight Sapphire theme).

Qt-free tests run everywhere (CI, sandbox). The Qt tests skip without PySide6
and run on the Windows machine, where they exercise the real widgets
offscreen — the only place the native app can actually be rendered.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui import theme  # noqa: E402  (pure Python, no Qt)

UI_MODULES = ("run_app.py", "md_to_widgets.py", "ui/decision_center.py",
              "ui/candidates_workbench.py", "ui/news_events.py", "ui/widgets.py")


# ---------------------------------------------------------------- theme ------
def _rules(qss: str) -> list[tuple[str, str]]:
    qss = re.sub(r"/\*.*?\*/", "", qss, flags=re.S)          # drop comments
    return [(" ".join(m.group(1).split()), m.group(2))
            for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", qss)]


def test_radius_scale_is_three_steps():
    qss = theme.build_qss()
    radii = {int(x) for x in re.findall(r"border-radius:\s*(\d+)px", qss)}
    # 0 = embedded tables (no frame), 3 = scroll-bar handles (10 px track).
    assert radii <= {0, 3, theme.RADIUS_SM, theme.RADIUS_MD, theme.RADIUS_LG}, radii
    assert (theme.RADIUS_SM, theme.RADIUS_MD, theme.RADIUS_LG) == (6, 10, 14)


@pytest.mark.parametrize("selector,expected", [
    ("QPushButton, QToolButton#Drawer", theme.RADIUS_MD),
    ("QLineEdit, QComboBox", theme.RADIUS_MD),
    ("QLabel#Pill", theme.RADIUS_MD),
    ("QPushButton#NavItem", theme.RADIUS_MD),
    ("QTabBar::tab", theme.RADIUS_MD),
    ("QFrame#Card", theme.RADIUS_LG),
    ("QFrame#Tile", theme.RADIUS_LG),
    ("QTableView, QTableWidget, QListView, QListWidget", theme.RADIUS_LG),
])
def test_every_control_family_uses_its_radius(selector, expected):
    rules = dict(_rules(theme.build_qss()))
    assert selector in rules, f"missing rule {selector}"
    m = re.search(r"border-radius:\s*(\d+)px", rules[selector])
    assert m and int(m.group(1)) == expected, (selector, rules[selector])


def test_inputs_and_buttons_share_one_height():
    rules = dict(_rules(theme.build_qss()))
    for sel in ("QPushButton, QToolButton#Drawer", "QLineEdit, QComboBox"):
        assert f"min-height: {theme.CONTROL_MIN_H}px" in rules[sel], sel
        assert "padding: 5px" in rules[sel], sel


def test_stylesheet_assets_exist():
    qss = theme.build_qss()
    paths = re.findall(r'url\("([^"]+)"\)', qss)
    assert paths, "combo / checkbox icons are referenced"
    for p in paths:
        assert Path(p).exists(), p


def test_no_hard_coded_colours_left_in_ui_modules():
    """Colours live in ui/theme.py only; one stray hex is how drift starts."""
    hexes = re.compile(r"#[0-9A-Fa-f]{6}\b")
    offenders = []
    for rel in UI_MODULES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and hexes.search(node.value):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, offenders


def test_dashboard_css_shares_the_desktop_tokens():
    """The browser dashboard takes its variables and chart colours from ui/theme.py."""
    import dashboard_html_builder as D
    tpl = D._TEMPLATE
    assert "/*__THEME_VARS__*/" in tpl and "__THEME_PAL__" in tpl
    css = theme.css_vars()
    assert f"--r-lg:{theme.RADIUS_LG}px" in css
    assert f"--r-md:{theme.RADIUS_MD}px" in css
    assert f"--r-sm:{theme.RADIUS_SM}px" in css
    for key in ("accent_a", "accent_b", "text", "text2", "brand_a"):
        assert theme.C[key] in css, key
    for tone in ("green", "amber", "red"):
        assert theme.TONES[tone]["fg"] in css, tone
    # No chart colour literals left in the dashboard script: they come from PAL.
    script = tpl[tpl.index("const DATA = JSON.parse"):]
    assert not re.findall(r'"#[0-9A-Fa-f]{6}"', script), "chart colours must use PAL.*"


def test_built_dashboard_contains_the_theme(tmp_path, monkeypatch):
    import dashboard_html_builder as D
    (tmp_path / "validation_status.json").write_text(
        json.dumps({"verdict": "Insufficient Independent History", "stats": {}}), encoding="utf-8")
    monkeypatch.setattr(D, "OUT", tmp_path)
    html = D.build().read_text(encoding="utf-8")
    assert "/*__THEME_VARS__*/" not in html and "__THEME_PAL__" not in html
    assert theme.C["accent_a"] in html and theme.TONES["amber"]["fg"] in html
    assert 'class="side"' in html and 'id="sideStatus"' in html


# ------------------------------------------------------------ risk flags -----
def test_clean_is_not_a_risk_flag():
    from core.ui_readers import is_risk_flag, risk_flag_mask
    assert not is_risk_flag("Clean")
    assert not is_risk_flag(" clean ")
    assert not is_risk_flag(None) and not is_risk_flag(float("nan")) and not is_risk_flag("")
    assert is_risk_flag("Overbought RSI")
    s = pd.Series(["Clean", "Overbought RSI", None, "", "Weak trend; High drawdown"])
    assert risk_flag_mask(s).tolist() == [False, True, False, False, True]


def test_engine_still_writes_clean_for_no_flag():
    # The helper encodes this convention; if the engine changes it, revisit.
    assert '"Clean"' in (ROOT / "nse_quant_engine.py").read_text(encoding="utf-8")


def test_ui_flag_consumers_use_the_helper():
    for rel in ("ui/candidates_workbench.py", "ui/decision_center.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "risk_flag_mask" in src, rel
        assert '["Risk_Flag"].astype(str).str.strip().ne("")' not in src, rel


# ------------------------------------------------------- dashboard fixes -----
def test_matured_delta_hidden_when_history_has_no_forward_returns(tmp_path):
    import dashboard_html_builder as D
    hist = tmp_path / "score_history.csv"
    pd.DataFrame({"Date": ["2020-01-01", "2020-01-02"], "Symbol": ["A", "A"]}).to_csv(hist, index=False)
    assert D._delta_matured_from_history(hist, 37840) is None


def test_matured_delta_still_computed_when_column_exists(tmp_path):
    import dashboard_html_builder as D
    hist = tmp_path / "h.csv"
    pd.DataFrame({"Date": ["2020-01-01"] * 3, "Net_Forward_Return": [0.1, None, 0.2]}).to_csv(hist, index=False)
    assert D._delta_matured_from_history(hist, 5) == 3


def test_dashboard_regime_comes_from_macro_context(tmp_path, monkeypatch):
    import dashboard_html_builder as D
    (tmp_path / "validation_status.json").write_text(
        json.dumps({"verdict": "Insufficient Independent History", "stats": {}}), encoding="utf-8")
    (tmp_path / "macro_context.json").write_text(json.dumps({"regime": "risk-off"}), encoding="utf-8")
    monkeypatch.setattr(D, "OUT", tmp_path)
    assert D._payload()["regime"] == "risk-off"
    (tmp_path / "macro_context.json").unlink()
    assert D._payload()["regime"] == "—", "absent input must show blank, not 'Neutral'"


# ------------------------------------------------------------- Qt (live) -----
def _qt_app():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # also covers missing system libs (libEGL)
        pytest.skip(f"PySide6.QtWidgets unavailable here: {exc}")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication(sys.argv[:1])


def test_sorted_table_inspects_the_highlighted_row(tmp_path):
    app = _qt_app()
    from PySide6.QtCore import Qt
    from ui.candidates_workbench import CandidatesWorkbench
    (tmp_path / "output").mkdir()
    pd.DataFrame([
        {"Symbol": "AAA", "Name": "A", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 1,
         "Confidence_Adjusted_Score": 90, "Final_Score": 80, "RSI_14": 40, "Risk_Flag": "Clean"},
        {"Symbol": "BBB", "Name": "B", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 2,
         "Confidence_Adjusted_Score": 80, "Final_Score": 70, "RSI_14": 9, "Risk_Flag": "Overbought RSI"},
        {"Symbol": "CCC", "Name": "C", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 3,
         "Confidence_Adjusted_Score": 70, "Final_Score": 60, "RSI_14": 75, "Risk_Flag": "Clean"},
    ]).to_csv(tmp_path / "output" / "latest_scores.csv", index=False)
    cw = CandidatesWorkbench(tmp_path, tmp_path / "output")
    cw.refresh()
    model = cw.table.model()
    headers = [model.headerData(c, Qt.Horizontal) for c in range(model.columnCount())]
    rsi_col = headers.index("RSI")
    cw.table.sortByColumn(rsi_col, Qt.DescendingOrder)
    cw.table.selectRow(0)
    app.processEvents()
    visible = cw.table.model().item(0, headers.index("Symbol")).text()
    assert visible == "CCC", "numeric sort: 75 > 40 > 9 (text sort would put 9 first)"
    assert cw._df_view.iloc[cw._selected_index()]["Symbol"] == visible
    cw.table.sortByColumn(0, Qt.AscendingOrder)
    assert cw.table.model().item(0, headers.index("Symbol")).text() == "AAA", "Rank sort = canonical order"
    # "Clean" rows are not flagged
    cw.cb_flag.setCurrentText("Flagged")
    assert cw._df_view["Symbol"].tolist() == ["BBB"]
    cw.cb_flag.setCurrentText("Clean")
    assert cw._df_view["Symbol"].tolist() == ["AAA", "CCC"]


def test_main_window_navigation_and_compact_mode():
    app = _qt_app()
    import run_app
    w = run_app.MainWindow()
    try:
        for i, (_label, _glyph, title, _sub) in enumerate(w.NAV):
            w._go_to(i)
            app.processEvents()
            assert w.tabs.currentIndex() == i
            if i in w._subgroups():          # Context / Advanced: title = sub-page
                assert w.lbl_breadcrumb.text() == title
            else:
                assert w.lbl_page_title.text() == title and not w.lbl_breadcrumb.text()
            assert w._nav_buttons[i].isChecked()
        for tw in (w.tabs, w.tab_context, w.tab_advanced):   # one navigation system
            assert tw.tabBar().isHidden()
        # Every nested page is reachable from the sidebar and remembered.
        for page, tw in w._subgroups().items():
            assert [b.text() for b in w._sub_buttons[page]] == [tw.tabText(j) for j in range(tw.count())]
            last = tw.count() - 1
            w._sub_buttons[page][last].click(); app.processEvents()
            assert w.tabs.currentIndex() == page and tw.currentIndex() == last
            assert w.lbl_page_title.text() == tw.tabText(last)
            assert not w._sub_boxes[page][0].isHidden()
            w._go_to(0); app.processEvents()
            assert w._sub_boxes[page][0].isHidden(), "sub-items show only under the active section"
            w._go_to(page); app.processEvents()
            assert tw.currentIndex() == last
        w.resize(1100, 800); app.processEvents()
        assert w._compact is True
        w.resize(1480, 920); app.processEvents()
        assert w._compact is False
        for name in ("btn_run", "btn_reload", "btn_browser", "btn_evidence", "btn_drawer",
                     "cb_shadow", "cb_fetch", "drawer", "tab_candidates", "tab_news"):
            assert hasattr(w, name), name
    finally:
        w.close()


# ------------------------------------------------- round 3 (Overview / HTML) ----
def test_overview_cuts_top_candidates_from_scores_when_trade_plan_lacks_eligibility():
    from core.candidate_selection import top_official_candidates
    from core.ui_readers import candidate_source
    trade = pd.DataFrame({"Symbol": ["AAA", "BBB"], "Opportunity_Rank": [1, 2]})   # no eligibility column
    scores = pd.DataFrame({"Symbol": ["AAA", "BBB"], "Opportunity_Rank": [1, 2],
                           "Opportunity_Eligible": ["Yes", "Yes"],
                           "Confidence_Adjusted_Score": [80, 70]})
    assert top_official_candidates(trade, 5).empty          # the old source: always empty
    assert top_official_candidates(candidate_source(trade, scores), 5)["Symbol"].tolist() == ["AAA", "BBB"]
    with_elig = trade.assign(Opportunity_Eligible=["Yes", "No"])
    assert candidate_source(with_elig, scores) is with_elig  # trade plan still wins when it can


def test_leading_glyph_becomes_the_marker():
    assert theme.split_marker("⚠ New risk flag: X") == ("▲", "New risk flag: X")
    assert theme.split_marker("⚠️ emoji variant") == ("▲", "emoji variant")
    assert theme.split_marker("↑ Top-5 entrants: A") == ("↑", "Top-5 entrants: A")
    assert theme.split_marker("Plain text") == ("●", "Plain text")


def test_review_queue_summarises_rank_moves_and_puts_red_first():
    src = (ROOT / "ui" / "decision_center.py").read_text(encoding="utf-8")
    fn = src[src.index("def _section_review_queue"):src.index("def _section_overlay_activation")]
    assert "Large rank move: {r.Symbol}" not in fn, "one line per symbol floods the 20-item cap"
    assert "large rank move" in fn and 'order = {"red": 0, "amber": 1}' in fn


def test_overview_order_top_candidates_under_verdict():
    src = (ROOT / "ui" / "decision_center.py").read_text(encoding="utf-8")
    two_col = src[src.index('grid.addWidget(sec["banner"],   0, 0, 1, 2)'):]
    assert two_col.index('sec["top"],      1, 0, 1, 2') < two_col.index('sec["progress"], 2, 0')
    assert 'sec["overlays"], 2, 1' in two_col and 'sec["changes"],  3, 0' in two_col \
        and 'sec["review"],   3, 1' in two_col


def test_feed_refresh_button_sits_in_the_card_header():
    src = (ROOT / "ui" / "decision_center.py").read_text(encoding="utf-8")
    fn = src[src.index("def _section_overlay_activation"):src.index("def _refresh_optional_feeds")]
    assert fn.index("Refresh now") < fn.index("Auto-refreshed each run")


def test_dashboard_sidebar_is_sticky_and_collapsible():
    import dashboard_html_builder as D
    tpl = D._TEMPLATE
    side_rule = re.search(r"\n\.side \{([^}]*)\}", tpl).group(1)
    assert "position:sticky" in side_rule and "position:fixed" not in side_rule
    assert 'id="sideToggle"' in tpl and ".side-collapsed .side" in tpl
    assert "embedded=1" in tpl
    app_src = (ROOT / "run_app.py").read_text(encoding="utf-8")
    assert 'url.setQuery("embedded=1")' in app_src
    assert "classList.add('embedded')" in app_src, "embedded mark also applied on load"
    assert ".embedded .side {display:none" in tpl, "inside the app the page has no second sidebar"


def test_nested_tab_bars_replaced_by_sidebar_subnav():
    src = (ROOT / "run_app.py").read_text(encoding="utf-8")
    assert "self.tab_context.tabBar().hide()" in src
    assert "self.tab_advanced.tabBar().hide()" in src
    assert "def _attach_subnav" in src and '"NavSubItem"' in src
    rules = dict(_rules(theme.build_qss()))
    m = re.search(r"border-radius:\s*(\d+)px", rules["QPushButton#NavSubItem"])
    assert m and int(m.group(1)) == theme.RADIUS_MD


# ------------------------------------------------------ round 5 (v5.0) ----
def test_single_app_version_5_0():
    from core.version import APP_VERSION
    assert APP_VERSION == "5.0"
    app = (ROOT / "run_app.py").read_text(encoding="utf-8")
    assert "from core.version import APP_VERSION" in app
    assert not re.search(r'^APP_VERSION\s*=', app, re.M), "no second version literal"
    assert '"app_version"' in (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    import core.evidence_bundle as eb
    assert f"v{APP_VERSION}" in eb._engine_version()


def test_dashboard_shows_app_version(tmp_path, monkeypatch):
    import dashboard_html_builder as D
    from core.version import APP_VERSION
    (tmp_path / "validation_status.json").write_text(json.dumps({"verdict": "x", "stats": {}}), encoding="utf-8")
    monkeypatch.setattr(D, "OUT", tmp_path)
    html = D.build().read_text(encoding="utf-8")
    assert "__APP_VERSION__" not in html and f"v{APP_VERSION}" in html


def test_display_number_formatting():
    from core.ui_readers import format_cell
    assert format_cell(935.4000244140624) == "935.40"
    assert format_cell(20.00000459516826) == "20.00"
    assert format_cell(137.0) == "137"
    assert format_cell(128152.82451) == "128,152.82"
    assert format_cell(0.000817) == "0.000817"
    assert format_cell(float("nan")) == "" and format_cell(None) == ""
    assert format_cell("INFY.NS") == "INFY.NS"


def test_embedded_dashboard_hides_its_sidebar_for_any_build():
    src = (ROOT / "run_app.py").read_text(encoding="utf-8")
    assert "nq-embedded-style" in src and "aside.side,.side{display:none!important}" in src
    assert "def _rebuild" in src and "dashboard_html_builder.py" in src


def test_raw_score_bucket_column_is_mapped():
    src = (ROOT / "ui" / "candidates_workbench.py").read_text(encoding="utf-8")
    assert '"Raw Score Bucket": "Bucket"' in src
    assert "_price_from(row)" in src, "Current price falls back to the scores row"


def test_ampersand_labels_render_literally():
    src = (ROOT / "run_app.py").read_text(encoding="utf-8")
    assert "QPushButton(W.button_text(label))" in src

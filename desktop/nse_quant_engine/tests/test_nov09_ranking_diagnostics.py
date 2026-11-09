"""Regression checks for ETF classification and benchmark diagnostics."""
import numpy as np
import pandas as pd
import universe_builder as ub
from core import portfolio_selection as ps
import trade_plan_builder as tb


def _etf(symbol, underlying, key=''):
    return pd.Series({'Symbol': symbol, 'Underlying Asset': underlying,
                      'SecurityName': symbol, 'ETF Underlying': 'EQUITY',
                      'Underlying Key': key})


def test_equity_and_nifty_are_not_technology_keywords():
    assert ub.infer_etf_category(_etf('NIFTYBEES', 'Nifty 50', 'Nifty 50')) == 'Broad Market ETF'
    assert ub.infer_etf_category(_etf('CPSEETF', 'Nifty CPSE', 'Nifty CPSE')) == 'PSU/CPSE ETF'
    assert ub.infer_etf_category(_etf('ITBEES', 'Nifty IT TRI', 'Nifty IT')) == 'IT ETF'
    assert ub.infer_etf_category(_etf('LIQUIDBEES', 'Overnight ETFs')) == 'Parking/Debt ETF'


def test_beta_uses_consistent_variance_normalization():
    dates = pd.bdate_range('2026-01-01', periods=70)
    br = np.linspace(-.02, .02, len(dates)-1)
    bm = 100*np.cumprod(np.r_[1., 1+br])
    st = 80*np.cumprod(np.r_[1., 1+2*br])
    prices = pd.concat([pd.DataFrame({'Date': dates, 'Symbol': sym, 'Close': close})
                        for sym, close in [('^NSEI', bm), ('X', st)]])
    beta = ps.benchmark_stats(prices, ['X']).iloc[0]['BetaVsBenchmark_63D']
    assert abs(beta - 2.0) < 1e-10


def test_excess_does_not_compare_different_missing_dates():
    dates = pd.bdate_range('2026-01-01', periods=70)
    benchmark = pd.DataFrame({'Date': dates, 'Symbol': '^NSEI', 'Close': 100 * 1.001**np.arange(70)})
    stock = pd.DataFrame({'Date': dates.delete(-4), 'Symbol': 'X', 'Close': 100 * 1.002**np.delete(np.arange(70), -4)})
    stats = ps.benchmark_stats(pd.concat([benchmark,stock]), ['X']).iloc[0]
    assert pd.isna(stats['Excess_21D'])


def test_trade_plan_reads_benchmark_from_index_cache(tmp_path):
    dates = pd.bdate_range('2026-01-01', periods=80)
    frames = [pd.DataFrame({'Date':dates,'Symbol':s,'Close':100*(1+r)**np.arange(80)})
              for s,r in [('A',.001),('B',.002)]]
    prices = pd.concat(frames); (tmp_path/'data').mkdir(); (tmp_path/'out').mkdir()
    prices.to_csv(tmp_path/'data'/'raw_prices_latest.csv',index=False)
    pd.DataFrame({'Date':dates,'Symbol':'^NSEI','Close':100*1.0005**np.arange(80)}).to_csv(tmp_path/'data'/'index_prices_latest.csv',index=False)
    old = (tb.RAW_PRICES,tb.DATA_DIR,tb.OUTPUT_DIR,tb.TOP5_CORR_CSV,tb.TOP5_BENCH_CSV)
    try:
        tb.RAW_PRICES=tmp_path/'data'/'raw_prices_latest.csv'; tb.DATA_DIR=tmp_path/'data'; tb.OUTPUT_DIR=tmp_path/'out'
        tb.TOP5_CORR_CSV=tb.OUTPUT_DIR/'top5_corr_matrix.csv'; tb.TOP5_BENCH_CSV=tb.OUTPUT_DIR/'top5_benchmark_stats.csv'
        plan=pd.DataFrame({'Symbol':['A','B'], 'Opportunity_Rank':[1,2], 'Confidence_Adjusted_Score':[90,80], 'Decision_Use':['WATCHLIST_ONLY']*2})
        tb._emit_corr_and_benchmark(plan)
        result=pd.read_csv(tb.TOP5_BENCH_CSV)
        assert len(result)==2 and result['Excess_21D'].notna().all()
    finally:
        tb.RAW_PRICES,tb.DATA_DIR,tb.OUTPUT_DIR,tb.TOP5_CORR_CSV,tb.TOP5_BENCH_CSV=old


def test_dashboard_alignment_does_not_hide_trade_plan_veto_mismatch():
    from dashboard_html_builder import _assert_top5_alignment
    tp = pd.DataFrame({'Symbol': ['A.NS','B.NS','C.NS','D.NS','ADANIPORTS.NS','E.NS'],
                       'Opportunity_Rank': [1,2,3,4,5,6],
                       'Confidence_Adjusted_Score': [90,89,88,87,86,85],
                       'Trade_Status': ['Watch only']*6})
    cards = [{'sym':s} for s in ['A.NS','B.NS','C.NS','D.NS','E.NS']]
    result = _assert_top5_alignment(cards, tp, 'Confidence_Adjusted_Score')
    assert result['ok'] is False
    assert result['plan'][-1] == 'ADANIPORTS'

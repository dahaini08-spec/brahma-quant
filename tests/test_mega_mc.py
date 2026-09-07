from brahma_os.mega_mc import bootstrap_trades, promote_mega


def test_bootstrap_runs_and_is_deterministic():
    pnls = [10.0, -8.0, 12.0, -9.0] * 5
    a = bootstrap_trades(pnls, n=2_000, seed=1, dd_sample=200)
    b = bootstrap_trades(pnls, n=2_000, seed=1, dd_sample=200)
    assert a.nav_p50 == b.nav_p50
    assert a.n == 2000


def test_losing_book_fails_promote():
    pnls = [-20.0] * 30
    s = bootstrap_trades(pnls, n=3_000, seed=2, dd_sample=300)
    ok, fails = promote_mega(s)
    assert ok is False
    assert fails

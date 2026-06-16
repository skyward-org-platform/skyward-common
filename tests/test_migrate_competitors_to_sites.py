from scripts.migrate_competitors_to_sites import plan_backfill


def test_single_brand_site_fans_to_that_site():
    competitor_rows = [(1, 99, "HIGH")]
    brand_sites = {1: [10]}
    inserts, multi_review, zero_review = plan_backfill(competitor_rows, brand_sites)
    assert inserts == [(10, 99, "HIGH")]
    assert multi_review == []
    assert zero_review == []


def test_multi_site_fans_out_and_flags_review():
    competitor_rows = [(1, 99, "NORMAL")]
    brand_sites = {1: [10, 11]}
    inserts, multi_review, zero_review = plan_backfill(competitor_rows, brand_sites)
    assert set(inserts) == {(10, 99, "NORMAL"), (11, 99, "NORMAL")}
    assert multi_review == [(1, 99, [10, 11])]
    assert zero_review == []


def test_zero_brand_site_is_skipped_and_flagged():
    competitor_rows = [(1, 99, "LOW")]
    brand_sites = {1: []}
    inserts, multi_review, zero_review = plan_backfill(competitor_rows, brand_sites)
    assert inserts == []
    assert multi_review == []
    assert zero_review == [(1, 99)]


def test_competitor_equal_to_brand_site_is_skipped():
    competitor_rows = [(1, 10, "HIGH")]
    brand_sites = {1: [10]}
    inserts, multi_review, zero_review = plan_backfill(competitor_rows, brand_sites)
    assert inserts == []

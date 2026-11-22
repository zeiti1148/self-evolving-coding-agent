"""Grouped ranking statistics: mixed averages hide the effect of the type signal."""
from scripts.evaluate_candidate_ranking import summary


ROWS = [
    dict(case='a', group='same-type', strategy='production', candidates=3, rank=1, expected_top=5, ms=2.0),
    dict(case='b', group='same-type', strategy='production', candidates=3, rank=2, expected_top=None, ms=4.0),
    dict(case='a', group='same-type', strategy='no-lexical', candidates=3, rank=1, expected_top=5, ms=1.0),
    dict(case='c', group='cross-type', strategy='production', candidates=3, rank=1, expected_top=None, ms=3.0),
    dict(case='d', group='cross-type', strategy='production', candidates=3, rank=3, expected_top=None, ms=5.0),
    dict(case='c', group='cross-type', strategy='no-lexical', candidates=3, rank=3, expected_top=None, ms=1.0),
]


def test_summary_is_split_by_group_and_strategy():
    values = summary(ROWS)
    assert set(values) == {'same-type/production', 'same-type/no-lexical',
                           'cross-type/production', 'cross-type/no-lexical'}
    same = values['same-type/production']
    assert same['cases'] == 2 and same['recall_at_1'] == 1 and same['recall_at_5'] == 2
    assert same['mean_rank'] == 1.5
    cross = values['cross-type/no-lexical']
    assert cross['recall_at_1'] == 0 and cross['mean_rank'] == 3.0


def test_summary_reports_missing_partner_as_not_eligible():
    rows = [dict(case='x', group='same-type', strategy='production', candidates=2, rank=None,
                 expected_top=None, ms=1.0)]
    value = summary(rows)['same-type/production']
    assert value['cases'] == 1 and value['eligible'] == 0 and value['mean_rank'] is None


def test_summary_is_empty_without_rows():
    assert summary([]) == {}

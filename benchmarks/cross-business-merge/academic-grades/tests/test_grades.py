import pytest
from grades import publish_grade


@pytest.mark.parametrize("assessments,expected,passed,scholarship", [
    ([{"score": "72.25", "weight": "1"}], "72.3", True, False),
    ([{"score": "85.05", "weight": "1"}], "85.1", True, True),
    ([{"score": "84.85", "weight": "1"}], "84.9", True, False),
    ([{"score": "89.85", "weight": "1"}], "89.9", True, True),
    ([{"score": "59.95", "weight": "1"}], "60.0", True, False),
    ([{"score": "84.95", "weight": "1"}], "85.0", True, True),
    ([{"score": "59.94", "weight": "1"}], "59.9", False, False),
    ([{"score": "72.20", "weight": "0.3"}, {"score": "72.30", "weight": "0.3"}], "72.3", True, False),
    ([{"score": "80", "weight": "2"}, {"score": "95", "weight": "1"}], "85.0", True, True),
    ([{"score": "0", "weight": "1"}], "0.0", False, False),
])
def test_published_mark(assessments, expected, passed, scholarship):
    assert publish_grade(assessments) == {"mark": expected, "passed": passed, "scholarship": scholarship}


@pytest.mark.parametrize("assessments", [[], [{"score": "101", "weight": "1"}], [{"score": "50", "weight": "0"}]])
def test_invalid_assessment(assessments):
    with pytest.raises(ValueError):
        publish_grade(assessments)

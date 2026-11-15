"""Publish a student's weighted mark and scholarship eligibility."""


def publish_grade(assessments):
    points = 0.0
    weight_sum = 0.0
    for assessment in assessments:
        score = float(assessment["score"])
        weight = float(assessment["weight"])
        if not 0 <= score <= 100 or weight <= 0:
            raise ValueError("invalid assessment")
        points += score * weight
        weight_sum += weight
    if weight_sum == 0:
        raise ValueError("no assessments")
    average = round(points / weight_sum, 1)
    return {"mark": format(average, ".1f"), "passed": average >= 60, "scholarship": average >= 85}

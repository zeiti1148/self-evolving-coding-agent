package dev.selfevolving.benchmark;

import java.util.List;

public class ScoreSummary {
    public int total(List<Integer> scores) {
        int result = 0;
        // Intentional defect: the list and its boxed values can be null.
        for (Integer score : scores) {
            result += score;
        }
        return result;
    }
}

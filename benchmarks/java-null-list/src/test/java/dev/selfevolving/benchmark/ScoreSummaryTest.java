package dev.selfevolving.benchmark;

import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class ScoreSummaryTest {
    private final ScoreSummary summary = new ScoreSummary();

    @Test void sumsOrdinaryScores() {
        assertEquals(10, summary.total(List.of(2, 3, 5)));
    }

    @Test void preservesNegativeValues() {
        assertEquals(-2, summary.total(List.of(-5, 3)));
    }

    @Test void emptyListHasZeroTotal() {
        assertEquals(0, summary.total(List.of()));
    }

    @Test void missingListHasZeroTotal() {
        assertEquals(0, summary.total(null));
    }

    @Test void missingElementsContributeZero() {
        assertEquals(7, summary.total(Arrays.asList(null, 3, null, 4)));
    }
}

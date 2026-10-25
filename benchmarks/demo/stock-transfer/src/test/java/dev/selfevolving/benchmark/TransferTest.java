package dev.selfevolving.benchmark;

import java.util.Map;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class TransferTest {
    private final TransferService service = new TransferService();
    private StockLedger ledger(int capacity, int books) { return new StockLedger(capacity, Map.of("book", books)); }

    @Test void requestRejectsBlankSku() {
        assertThrows(IllegalArgumentException.class, () -> new TransferRequest("  ", 1).validate());
        assertThrows(IllegalArgumentException.class, () -> new TransferRequest(null, 1).validate());
    }
    @Test void requestRejectsZeroQuantity() {
        assertThrows(IllegalArgumentException.class, () -> new TransferRequest("book", 0).validate());
    }
    @Test void validRequestIsAccepted() { assertDoesNotThrow(() -> new TransferRequest("book", 1).validate()); }
    @Test void canWithdrawExactlyAvailableQuantity() {
        StockLedger source = ledger(20, 5); source.withdraw("book", 5); assertEquals(0, source.quantity("book"));
    }
    @Test void negativeWithdrawalCannotCreateStock() {
        StockLedger source = ledger(20, 5);
        assertThrows(IllegalArgumentException.class, () -> source.withdraw("book", -2));
        assertEquals(5, source.quantity("book"));
    }
    @Test void nonpositiveDepositIsRejected() {
        StockLedger target = ledger(20, 5);
        assertThrows(IllegalArgumentException.class, () -> target.deposit("book", 0));
        assertThrows(IllegalArgumentException.class, () -> target.deposit("book", -2));
        assertEquals(5, target.quantity("book"));
    }
    @Test void ledgerCapacityIncludesOtherProducts() {
        StockLedger target = new StockLedger(8, Map.of("book", 3, "pen", 4));
        assertThrows(IllegalArgumentException.class, () -> target.deposit("book", 2));
        assertEquals(7, target.total());
    }
    @Test void successfulTransferUpdatesBothLedgers() {
        StockLedger source = ledger(20, 10), target = ledger(20, 3);
        service.transfer(source, target, new TransferRequest("book", 4));
        assertEquals(6, source.quantity("book")); assertEquals(7, target.quantity("book"));
    }
    @Test void insufficientSourceLeavesBothUnchanged() {
        StockLedger source = ledger(20, 3), target = ledger(20, 2);
        assertThrows(IllegalArgumentException.class, () -> service.transfer(source, target, new TransferRequest("book", 4)));
        assertEquals(3, source.quantity("book")); assertEquals(2, target.quantity("book"));
    }
    @Test void fullTargetMustNotConsumeSourceStock() {
        StockLedger source = ledger(20, 10), target = ledger(5, 4);
        assertThrows(IllegalArgumentException.class, () -> service.transfer(source, target, new TransferRequest("book", 3)));
        assertEquals(10, source.quantity("book")); assertEquals(4, target.quantity("book"));
    }
    @Test void sameLedgerTransferIsNoOp() {
        StockLedger same = ledger(5, 5);
        service.transfer(same, same, new TransferRequest("book", 5));
        assertEquals(5, same.quantity("book"));
    }
    @Test void serviceValidatesBeforeChangingEitherLedger() {
        StockLedger source = ledger(20, 5), target = ledger(20, 1);
        assertThrows(IllegalArgumentException.class, () -> service.transfer(source, target, new TransferRequest("book", 0)));
        assertEquals(5, source.quantity("book")); assertEquals(1, target.quantity("book"));
        assertThrows(IllegalArgumentException.class, () -> service.transfer(source, source, new TransferRequest("book", 0)));
    }
}

package dev.selfevolving.benchmark;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class PointsGiftTest {
    private final PointsGiftService service = new PointsGiftService();
    @Test void zeroGiftIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> new GiftRequest(0, 0).validate());
    }
    @Test void negativeGiftIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> new GiftRequest(-1, 0).validate());
    }
    @Test void negativeFeeIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> new GiftRequest(200, -1).validate());
    }
    @Test void totalDebitIncludesFee() {
        assertEquals(210, new GiftRequest(200, 10).totalDebit());
    }
    @Test void debitRejectsNonpositiveAmount() {
        PointsAccount sender = new PointsAccount(1000, true);
        assertThrows(IllegalArgumentException.class, () -> sender.debit(0));
        assertThrows(IllegalArgumentException.class, () -> sender.debit(-10));
        assertEquals(1000, sender.balance());
    }
    @Test void exactBalanceCanBeDebited() {
        PointsAccount sender = new PointsAccount(210, true);
        sender.debit(210); assertEquals(0, sender.balance());
    }
    @Test void creditRejectsNonpositiveAmount() {
        PointsAccount recipient = new PointsAccount(100, true);
        assertThrows(IllegalArgumentException.class, () -> recipient.credit(0));
        assertThrows(IllegalArgumentException.class, () -> recipient.credit(-10));
        assertEquals(100, recipient.balance());
    }
    @Test void giftChargesFeeOnlyToSender() {
        PointsAccount sender = new PointsAccount(1000, true), recipient = new PointsAccount(100, true);
        service.give(sender, recipient, new GiftRequest(200, 10));
        assertEquals(790, sender.balance()); assertEquals(300, recipient.balance());
    }
    @Test void disabledRecipientMustNotConsumeSenderPoints() {
        PointsAccount sender = new PointsAccount(1000, true), recipient = new PointsAccount(100, false);
        assertThrows(IllegalArgumentException.class, () -> service.give(sender, recipient, new GiftRequest(200, 0)));
        assertEquals(1000, sender.balance()); assertEquals(100, recipient.balance());
    }
    @Test void balanceMustCoverGiftAndFee() {
        PointsAccount sender = new PointsAccount(205, true), recipient = new PointsAccount(100, true);
        assertThrows(IllegalArgumentException.class, () -> service.give(sender, recipient, new GiftRequest(200, 10)));
        assertEquals(205, sender.balance()); assertEquals(100, recipient.balance());
    }
    @Test void selfGiftIsRejectedBeforeChangingBalance() {
        PointsAccount same = new PointsAccount(1000, true);
        assertThrows(IllegalArgumentException.class, () -> service.give(same, same, new GiftRequest(200, 10)));
        assertEquals(1000, same.balance());
    }
    @Test void validGiftWithNoFeeAndInvalidRequestAreHandled() {
        PointsAccount sender = new PointsAccount(1000, true), recipient = new PointsAccount(100, true);
        service.give(sender, recipient, new GiftRequest(200, 0));
        assertEquals(800, sender.balance()); assertEquals(300, recipient.balance());
        assertThrows(IllegalArgumentException.class, () -> service.give(sender, recipient, new GiftRequest(0, 0)));
        assertEquals(800, sender.balance()); assertEquals(300, recipient.balance());
    }
}

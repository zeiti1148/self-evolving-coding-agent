package dev.selfevolving.benchmark;

public class PointsGiftService {
    public void give(PointsAccount sender, PointsAccount recipient, GiftRequest request) {
        sender.debit(request.totalDebit());
        recipient.credit(request.points());
    }
}

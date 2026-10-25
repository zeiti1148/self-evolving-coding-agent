package dev.selfevolving.benchmark;

public class AddressLabel {
    public String render(Address address) {
        // Intentional training defect: absent address data causes an exception.
        return "ZIP " + address.zip().intValue();
    }
}

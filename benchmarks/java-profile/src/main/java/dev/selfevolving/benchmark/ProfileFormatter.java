package dev.selfevolving.benchmark;

public class ProfileFormatter {
    public String format(UserProfile profile) {
        // Intentional defect: both profile and its nested display name can be null.
        return "User: " + profile.displayName().trim();
    }
}

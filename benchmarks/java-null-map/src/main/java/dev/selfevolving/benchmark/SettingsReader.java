package dev.selfevolving.benchmark;

import java.util.Locale;
import java.util.Map;

public class SettingsReader {
    public String language(Map<String, String> settings) {
        // Intentional defect: both the map and the requested value can be absent.
        return settings.get("language").trim().toLowerCase(Locale.ROOT);
    }
}

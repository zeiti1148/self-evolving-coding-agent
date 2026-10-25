package dev.selfevolving.benchmark;

import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class SettingsReaderTest {
    private final SettingsReader reader = new SettingsReader();

    @Test void normalizesConfiguredLanguage() {
        assertEquals("fr", reader.language(Map.of("language", " FR ")));
    }

    @Test void preservesAnExistingLanguageCode() {
        assertEquals("zh-cn", reader.language(Map.of("language", "zh-CN", "theme", "dark")));
    }

    @Test void missingMapUsesEnglish() {
        assertEquals("en", reader.language(null));
    }

    @Test void missingLanguageUsesEnglish() {
        assertEquals("en", reader.language(Map.of("theme", "dark")));
    }

    @Test void nullLanguageUsesEnglish() {
        Map<String, String> settings = new HashMap<>();
        settings.put("language", null);
        assertEquals("en", reader.language(settings));
    }
}

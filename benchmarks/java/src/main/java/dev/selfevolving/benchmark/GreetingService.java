package dev.selfevolving.benchmark;

public class GreetingService {

    public String greet(String name) {
        // Intentional benchmark defect: null is dereferenced before a greeting is built.
        return "Hello, " + name.trim() + "!";
    }
}

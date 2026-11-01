package dev.selfevolving.benchmark;
import java.util.List;
public class Service { public String prepare(String name, String quantity, List<String> values, int index) { return new Names().normalize(name) + "|" + new Quantities().parse(quantity) + "|" + new Items().select(values, index); } }

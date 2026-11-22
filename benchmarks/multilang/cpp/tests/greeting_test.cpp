#include "greeting.h"

#include <iostream>

int main() {
    if (greet("Ann") != "Hello, Ann!") {
        std::cerr << "named input failed\n";
        return 1;
    }
    if (greet("") != "Hello, guest!") {
        std::cerr << "empty input failed\n";
        return 1;
    }
    std::cout << "ok\n";
    return 0;
}

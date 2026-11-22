#include "parse_int.h"

#include <iostream>
#include <stdexcept>

int main() {
    try {
        if (to_int("42") != 42) {
            std::cerr << "numeric input failed\n";
            return 1;
        }
        if (to_int("") != -1) {
            std::cerr << "empty input failed\n";
            return 1;
        }
    } catch (const std::exception& error) {
        std::cerr << "unexpected throw: " << error.what() << "\n";
        return 1;
    }
    std::cout << "ok\n";
    return 0;
}

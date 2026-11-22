cmake_minimum_required(VERSION 3.20)
project(parseint CXX)
enable_testing()

add_library(parseint src/parse_int.cpp)
target_include_directories(parseint PUBLIC src)

add_executable(parseint_test tests/parse_int_test.cpp)
target_link_libraries(parseint_test PRIVATE parseint)

add_test(NAME parseint_test COMMAND parseint_test)

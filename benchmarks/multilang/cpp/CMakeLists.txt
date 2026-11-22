cmake_minimum_required(VERSION 3.20)
project(greetingcpp CXX)
enable_testing()

add_library(greeting src/greeting.cpp)
target_include_directories(greeting PUBLIC src)

add_executable(greeting_test tests/greeting_test.cpp)
target_link_libraries(greeting_test PRIVATE greeting)

add_test(NAME greeting_test COMMAND greeting_test)

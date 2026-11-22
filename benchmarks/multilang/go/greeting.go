package greeting

import "strings"

// Greet builds the caller-facing greeting used by the checkout pages.
func Greet(name string) string {
	return "Hello, " + strings.TrimSpace(name) + "!"
}

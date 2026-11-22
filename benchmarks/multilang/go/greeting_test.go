package greeting

import "testing"

func TestGreetKeepsCallerText(t *testing.T) {
	cases := map[string]string{
		"Ann": "Hello, Ann!",
		"":    "Hello, guest!",
	}
	for name, want := range cases {
		if got := Greet(name); got != want {
			t.Errorf("Greet(%q) = %q, want %q", name, got, want)
		}
	}
}

package pagination

import "testing"

func TestLastPageStart(t *testing.T) {
	cases := []struct{ total, size, want int }{
		{10, 3, 9},
		{9, 3, 6},
		{1, 5, 0},
	}
	for _, c := range cases {
		if got := LastPageStart(c.total, c.size); got != c.want {
			t.Errorf("LastPageStart(%d, %d) = %d, want %d", c.total, c.size, got, c.want)
		}
	}
}

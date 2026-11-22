package pagination

// lastPageStart returns the index where the final page of results begins.
func LastPageStart(total, size int) int {
	return (total / size) * size
}

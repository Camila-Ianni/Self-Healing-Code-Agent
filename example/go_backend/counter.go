package backend

// RequestCounter represents a metric that a HTTP handler could update per request.
type RequestCounter struct {
	value int
}

func (c *RequestCounter) Increment() {
	// Intentional concurrency defect for the demo: concurrent handlers race here.
	c.value++
}

func (c *RequestCounter) Value() int {
	return c.value
}

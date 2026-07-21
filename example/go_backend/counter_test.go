package backend

import (
	"sync"
	"testing"
)

func TestRequestCounterIsSafeForConcurrentHandlers(t *testing.T) {
	var counter RequestCounter
	const workersCount = 32
	const incrementsPerWorker = 10_000

	var workers sync.WaitGroup
	start := make(chan struct{})
	workers.Add(workersCount)
	for range workersCount {
		go func() {
			defer workers.Done()
			<-start
			for range incrementsPerWorker {
				counter.Increment()
			}
		}()
	}
	close(start)
	workers.Wait()

	if got, want := counter.Value(), workersCount*incrementsPerWorker; got != want {
		t.Fatalf("count = %d, want %d", got, want)
	}
}

package aggregator

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestFetchAndAggregate(t *testing.T) {
	// Spin up mock external APIs
	serverA := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(Quote{Market: "BTC-USD", Price: 65000.0})
	}))
	defer serverA.Close()

	serverB := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(Quote{Market: "BTC-USD", Price: 65100.0})
	}))
	defer serverB.Close()

	serverC := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(Quote{Market: "ETH-USD", Price: 3400.0})
	}))
	defer serverC.Close()

	urls := []string{serverA.URL, serverB.URL, serverC.URL}

	type result struct {
		prices map[string]float64
		err    error
	}

	done := make(chan result, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	go func() {
		prices, err := FetchAndAggregate(ctx, urls)
		done <- result{prices, err}
	}()

	select {
	case got := <-done:
		if got.err != nil {
			t.Fatalf("FetchAndAggregate failed: %v", got.err)
		}
		if got.prices["BTC-USD"] != 65100.0 {
			t.Errorf("Expected BTC-USD price 65100.0, got %f", got.prices["BTC-USD"])
		}
		if got.prices["ETH-USD"] != 3400.0 {
			t.Errorf("Expected ETH-USD price 3400.0, got %f", got.prices["ETH-USD"])
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("timeout: goroutines blocked in deadlock due to incorrect coordination of WaitGroup and channel")
	}
}

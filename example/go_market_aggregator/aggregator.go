package aggregator

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"
)

// Quote represents a financial market price feed quote.
type Quote struct {
	Market string  `json:"market"`
	Price  float64 `json:"price"`
}

// FetchAndAggregate queries multiple external API endpoints concurrently to find the best prices.
// It has a deliberate concurrency deadlock: worker goroutines block trying to send to the
// unbuffered quotes channel, while the main thread waits for them to finish with wg.Wait().
func FetchAndAggregate(ctx context.Context, urls []string) (map[string]float64, error) {
	client := &http.Client{Timeout: 2 * time.Second}
	quotes := make(chan Quote)
	var wg sync.WaitGroup
	var errs = make(chan error, len(urls)) // Buffered channel for concurrent errors

	for _, url := range urls {
		wg.Add(1)
		go func(targetURL string) {
			defer wg.Done()

			req, err := http.NewRequestWithContext(ctx, "GET", targetURL, nil)
			if err != nil {
				errs <- err
				return
			}

			resp, err := client.Do(req)
			if err != nil {
				errs <- err
				return
			}
			defer resp.Body.Close()

			if resp.StatusCode != http.StatusOK {
				errs <- fmt.Errorf("feed server returned status %d", resp.StatusCode)
				return
			}

			var quote Quote
			if err := json.NewDecoder(resp.Body).Decode(&quote); err != nil {
				errs <- err
				return
			}

			// BUG: Writing to an unbuffered channel blocks because no receiver is reading yet.
			quotes <- quote
		}(url)
	}

	// Blocks main goroutine until all workers complete.
	// But workers never complete because they are blocked sending to 'quotes'.
	wg.Wait()
	close(quotes)
	close(errs)

	if len(errs) > 0 {
		return nil, <-errs
	}

	bestPrices := make(map[string]float64)
	for quote := range quotes {
		if quote.Price > bestPrices[quote.Market] {
			bestPrices[quote.Market] = quote.Price
		}
	}

	return bestPrices, nil
}

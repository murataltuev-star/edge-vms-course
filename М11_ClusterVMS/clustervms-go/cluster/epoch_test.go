package cluster

// Lesson 3 — CAS issuer, ACL, and the lease that fences.

import (
	"errors"
	"sort"
	"sync"
	"testing"
)

func TestCasIssuerNeverReusesANumber(t *testing.T) {
	v := NewFakeVariables()
	var mu sync.Mutex
	var issued []int64
	var wg sync.WaitGroup
	for g := 0; g < 4; g++ { // four goroutines racing, really in parallel
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 50; i++ {
				e, _, err := NextEpoch(v, "node-3")
				if err != nil {
					t.Error(err)
					return
				}
				mu.Lock()
				issued = append(issued, e)
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	sort.Slice(issued, func(i, j int) bool { return issued[i] < issued[j] })
	for i, e := range issued {
		if e != int64(i+1) {
			t.Fatalf("issued[%d] = %d", i, e)
		}
	}
	if cur, _ := CurrentEpoch(v, "node-3"); cur != 200 || len(issued) != 200 {
		t.Fatalf("current %d, issued %d", cur, len(issued))
	}
}

func TestStaleCasIsA409NeverASilentOverwrite(t *testing.T) {
	v := NewFakeVariables()
	_, idx, _ := v.Get("x")
	if _, err := v.Put("x", Items{"a": "1"}, idx); err != nil {
		t.Fatal(err)
	}
	if _, err := v.Put("x", Items{"a": "2"}, idx); !errors.Is(err, ErrConflict) {
		t.Fatalf("must conflict, got %v", err)
	}
}

func TestOneWriterPerKey(t *testing.T) {
	v := NewFakeVariables()
	v.SetACL("node-3", "nodes/node-3", "nodes/node-3/*")
	v.SetACL("node-4", "nodes/node-4", "nodes/node-4/*")
	n3 := v.AsWriter("node-3")
	if _, _, err := NextEpoch(n3, "node-3"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := NextEpoch(n3, "node-4"); !errors.Is(err, ErrForbidden) {
		t.Fatalf("node-3 must not issue node-4's epoch: %v", err)
	}
}

func TestLeaseFencesWhenANewerEpochAppears(t *testing.T) {
	v, clk := NewFakeVariables(), &fakeClock{1000}
	e, _, _ := NextEpoch(v, "node-3")
	lease := NewLease(v, "node-3", e, 30, 5, clk.now)
	if !lease.Renew() || !lease.MayWrite() {
		t.Fatal("fresh lease must hold")
	}
	NextEpoch(v, "node-3") // a replacement was issued the next epoch
	if lease.Renew() || !lease.Fenced || lease.Conflicts != 1 || lease.MayWrite() {
		t.Fatal("must fence, once")
	}
	if lease.Renew() || lease.Conflicts != 1 { // fenced stays fenced; counted once
		t.Fatal("fenced stays fenced")
	}
}

func TestLeaseStopsWritingAtTTLMinusMarginWithoutRenewal(t *testing.T) {
	v, clk := NewFakeVariables(), &fakeClock{1000}
	e, _, _ := NextEpoch(v, "node-3")
	lease := NewLease(v, "node-3", e, 30, 5, clk.now)
	clk.advance(24.9)
	if !lease.MayWrite() { // 24.9 < 25
		t.Fatal("inside TTL−margin")
	}
	clk.advance(0.2)
	if lease.MayWrite() || lease.SecondsLeft() != 0 { // a purely local decision
		t.Fatal("must stop at TTL−margin")
	}
	if !lease.Renew() || !lease.MayWrite() { // a successful renewal restores it
		t.Fatal("renewal restores")
	}
}

type downVars struct{ Variables }

func (downVars) Get(string) (Items, int64, error) { return nil, 0, errors.New("partitioned") }

func TestUnreachableClusterKeepsTheLeaseOnlyUntilTheMargin(t *testing.T) {
	v, clk := NewFakeVariables(), &fakeClock{1000}
	e, _, _ := NextEpoch(v, "node-3")
	lease := NewLease(v, "node-3", e, 30, 5, clk.now)
	lease.Vars = downVars{}
	clk.advance(10)
	if !lease.Renew() { // still inside TTL−margin: keep recording
		t.Fatal("keep recording")
	}
	clk.advance(20)
	if lease.Renew() || lease.Fenced { // expired, not fenced: nobody issued a new epoch (that we could see)
		t.Fatal("expired but not fenced")
	}
}

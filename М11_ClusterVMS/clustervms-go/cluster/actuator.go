package cluster

// Actuator is verb -> success: "start", "restart", "stop". In М10 it is
// GStreamer (and in a Go controller, a client of a C++ media worker — the
// per-frame rule survives cgo). It carries the Settings because the epoch
// is in every segment path it opens.

import "sync"

type Actuator interface {
	Actuate(verb string, cam CameraRow) bool
	StopAll()
	SetSettings(Settings)
	Settings() Settings
}

// FakeActuator is М10 Lesson 1's print(), grown a memory so tests can
// assert on it. Failing is the set of camera ids whose start fails.
type FakeActuator struct {
	mu       sync.Mutex
	Failing  map[int64]bool
	Calls    []Call
	Running  map[int64]bool
	settings Settings
}

type Call struct {
	Verb string
	ID   int64
}

func NewFakeActuator() *FakeActuator {
	return &FakeActuator{Failing: map[int64]bool{}, Running: map[int64]bool{}}
}

func (f *FakeActuator) Actuate(verb string, cam CameraRow) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Calls = append(f.Calls, Call{verb, cam.ID})
	if verb == "stop" {
		delete(f.Running, cam.ID)
		return true
	}
	if f.Failing[cam.ID] {
		delete(f.Running, cam.ID)
		return false
	}
	f.Running[cam.ID] = true
	return true
}

func (f *FakeActuator) StopAll() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Running = map[int64]bool{}
}

func (f *FakeActuator) SetSettings(s Settings) { f.settings = s }
func (f *FakeActuator) Settings() Settings     { return f.settings }

// RunningIDs is a sorted copy, for assertions.
func (f *FakeActuator) RunningIDs() []int64 {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]int64, 0, len(f.Running))
	for id := range f.Running {
		out = append(out, id)
	}
	sortInt64s(out)
	return out
}

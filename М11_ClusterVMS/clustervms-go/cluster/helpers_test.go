package cluster

import (
	"os"
	"testing"
)

// world is conftest.py's: a Variables raft in memory and an object store on disk.
func world(t *testing.T) (*FakeVariables, *FsObjectStore) {
	t.Helper()
	objs, err := NewFsObjectStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return NewFakeVariables(), objs
}

type fakeClock struct{ t float64 }

func (c *fakeClock) now() float64      { return c.t }
func (c *fakeClock) advance(s float64) { c.t += s }

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func testSettings() Settings {
	os.Setenv("ARCHIVE_DIR", "/tmp/clustervms-test-archive")
	defer os.Unsetenv("ARCHIVE_DIR")
	return SettingsFromEnv()
}

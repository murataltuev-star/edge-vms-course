package cluster

// Lesson 4 — a fenced instance's footage is re-indexed with its epoch, never deleted.

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

type memFs struct{ files map[string][2]float64 } // path -> size, mtime

func (m *memFs) put(p string, size, mtime float64) { m.files[p] = [2]float64{size, mtime} }
func (m *memFs) WalkFiles(root string) []FileInfo {
	var out []FileInfo
	for p, f := range m.files {
		if strings.HasPrefix(p, root) {
			out = append(out, FileInfo{p, f[1]})
		}
	}
	return out
}
func (m *memFs) Size(p string) int64 { return int64(m.files[p][0]) }

const arch = "/data/archive"

func utc(s string) time.Time {
	t, err := time.Parse("2006-01-02T15:04:05", s)
	if err != nil {
		panic(err)
	}
	return t.UTC()
}

func seg(fs *memFs, cam, epoch int64, start string, seconds float64) string {
	st := utc(start)
	p := fmt.Sprintf("%s/%d/e%d/%s.mp4", arch, cam, epoch, st.Format("20060102T150405Z"))
	fs.put(p, 1000, float64(st.Unix())+seconds)
	return p
}

func TestParse(t *testing.T) {
	cam, epoch, start, ok := Parse(arch+"/7/e5/20260908T091000Z.mp4", arch)
	if !ok || cam != 7 || epoch != 5 || !start.Equal(utc("2026-09-08T09:10:00")) {
		t.Fatal(cam, epoch, start, ok)
	}
	if _, _, _, ok := Parse(arch+"/7/e5/index.json", arch); ok {
		t.Fatal("junk")
	}
	if _, _, _, ok := Parse(arch+"/7/20260908T091000Z.mp4", arch); ok {
		t.Fatal("no epoch dir")
	}
}

func TestFencedSegmentsComeBackWithTheirEpoch(t *testing.T) {
	store, fs := NewFakeClusterStore(), &memFs{map[string][2]float64{}}
	now := float64(utc("2026-09-08T12:00:00").Unix())
	live := seg(fs, 7, 6, "2026-09-08T10:00:00", 600) // the replacement's, already indexed
	store.IndexSegment(Segment{7, utc("2026-09-08T10:00:00"), utc("2026-09-08T10:10:00"), live, 1000, 6})
	z1 := seg(fs, 7, 5, "2026-09-08T10:00:00", 600) // the zombie's, after the fence
	z2 := seg(fs, 7, 5, "2026-09-08T10:10:00", 600)
	seg(fs, 7, 6, "2026-09-08T11:55:00", 240) // closed 1 minute ago: leave it
	fs.put(arch+"/7/e6/index.json", 10, now-9999)
	rep, err := Sweep(store, fs, arch, 6, 600, now)
	must(t, err)
	if rep.Reindexed != 2 || rep.Fenced != 2 || rep.SkippedOpen != 1 || rep.SkippedUnparsable != 1 {
		t.Fatalf("%+v", rep)
	}
	rows := map[string]Segment{}
	for _, s := range store.Segments {
		rows[s.Path] = s
	}
	if rows[z1].Epoch != 5 || rows[z2].Epoch != 5 { // epoch kept from the path
		t.Fatal("epoch")
	}
	if !rows[z1].Start.Equal(utc("2026-09-08T10:00:00")) || !rows[z1].End.Equal(utc("2026-09-08T10:10:00")) {
		t.Fatal(rows[z1])
	}
	found := false
	for _, e := range store.Events {
		found = found || (e.Kind == "archive.reindexed" && e.Payload["fenced"] == 2)
	}
	if !found {
		t.Fatal("event")
	}
	rep2, _ := Sweep(store, fs, arch, 6, 600, now) // idempotent: a second sweep finds nothing new
	if rep2.Reindexed != 0 {
		t.Fatal("idempotent")
	}
}

// Lesson 3: the index did not travel. When the dead server's disks come
// back, the same sweep rebuilds it — nothing fenced about these, epoch == current.
func TestReturnedServerArchiveIsRebuiltFromSegments(t *testing.T) {
	store, fs := NewFakeClusterStore(), &memFs{map[string][2]float64{}}
	now := float64(utc("2026-09-09T00:00:00").Unix())
	for h := 0; h < 6; h++ {
		seg(fs, 7, 6, fmt.Sprintf("2026-09-08T%02d:00:00", h), 600)
	}
	rep, err := Sweep(store, fs, arch, 6, 600, now)
	must(t, err)
	if rep.Reindexed != 6 || rep.Fenced != 0 {
		t.Fatalf("%+v", rep)
	}
}

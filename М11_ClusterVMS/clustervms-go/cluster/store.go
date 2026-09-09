package cluster

// Store is М10's PgStore as ClusterVMS sees it: the statements the host
// touches, plus the three ClusterVMS adds (dump, revision, restore). The
// Postgres implementation lives behind the `pg` build tag (pgstore.go);
// FakeClusterStore is the same contract in memory.

import (
	"sort"
	"strconv"
	"strings"
	"time"
)

type Segment struct {
	Camera     int64
	Start, End time.Time
	Path       string
	Size       int64
	Epoch      int64
}

type StatusRow struct {
	Camera           int64
	ObservedRevision int64
	Phase            string
}

type Condition struct {
	Status bool
	Reason string // "" when there is nothing to explain
}

type Event struct {
	Kind    string
	Camera  *int64
	Payload map[string]any
}

type Store interface {
	// М10 surface
	Migrate(dir string) (bool, error)
	FetchDesired() ([]CameraRow, error)
	Report(rows []StatusRow) error
	SetCondition(camera int64, condition string, status bool, reason string) error
	IndexSegment(seg Segment) error
	IndexedPathsUnder(prefix string) (map[string]bool, error)
	LogEvent(kind string, camera *int64, payload map[string]any) error
	Cameras() ([]CameraRow, error)
	// ClusterVMS surface
	DumpConfig() ([]byte, error)
	ConfigRevision() (int64, error)
	IsUnconfigured() (bool, error)
	RestoreConfig(blob []byte) (int64, error)
}

// FakeClusterStore is the Store in memory: the tests, the baseline, and
// nothing else. Not safe for concurrent use beyond what the host does
// (one goroutine per task, each call short).
type FakeClusterStore struct {
	Rows       map[int64]CameraRow
	Sites      []Site
	Reports    [][]StatusRow
	Conditions map[[2]string]Condition // key: {camera id, condition}
	Segments   []Segment
	Events     []Event
	Migrated   int
}

func NewFakeClusterStore(cameras ...CameraRow) *FakeClusterStore {
	s := &FakeClusterStore{Rows: map[int64]CameraRow{}, Conditions: map[[2]string]Condition{}}
	for _, c := range cameras {
		s.Rows[c.ID] = c
	}
	if len(cameras) > 0 {
		s.Sites = []Site{{"hq", "hq"}}
	}
	return s
}

// Cam is the tests' one-line camera row.
func Cam(id int64, revision int64) CameraRow {
	return CameraRow{ID: id, SiteID: "hq", Name: "cam" + itoa(id), RtspURL: "rtsp://10.0.0." + itoa(id) + "/s",
		Enabled: true, RetentionDays: 30, Priority: 100, Revision: revision}
}

func (s *FakeClusterStore) sorted() []CameraRow {
	out := make([]CameraRow, 0, len(s.Rows))
	for _, r := range s.Rows {
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (s *FakeClusterStore) Migrate(string) (bool, error)       { s.Migrated++; return true, nil }
func (s *FakeClusterStore) FetchDesired() ([]CameraRow, error) { return s.sorted(), nil }
func (s *FakeClusterStore) Cameras() ([]CameraRow, error)      { return s.sorted(), nil }
func (s *FakeClusterStore) Report(rows []StatusRow) error {
	s.Reports = append(s.Reports, rows)
	return nil
}
func (s *FakeClusterStore) SetCondition(cam int64, cond string, ok bool, reason string) error {
	s.Conditions[[2]string{itoa(cam), cond}] = Condition{ok, reason}
	return nil
}
func (s *FakeClusterStore) IndexSegment(seg Segment) error {
	s.Segments = append(s.Segments, seg)
	return nil
}
func (s *FakeClusterStore) IndexedPathsUnder(prefix string) (map[string]bool, error) {
	out := map[string]bool{}
	for _, seg := range s.Segments {
		if strings.HasPrefix(seg.Path, prefix) {
			out[seg.Path] = true
		}
	}
	return out, nil
}
func (s *FakeClusterStore) LogEvent(kind string, cam *int64, payload map[string]any) error {
	s.Events = append(s.Events, Event{kind, cam, payload})
	return nil
}
func (s *FakeClusterStore) DumpConfig() ([]byte, error) {
	return Encode(s.Sites, s.sorted(), nil, nil)
}
func (s *FakeClusterStore) ConfigRevision() (int64, error) {
	var rev int64
	for _, r := range s.Rows {
		if r.Revision > rev {
			rev = r.Revision
		}
	}
	return rev, nil
}
func (s *FakeClusterStore) IsUnconfigured() (bool, error) { return len(s.Rows) == 0, nil }
func (s *FakeClusterStore) RestoreConfig(blob []byte) (int64, error) {
	c, err := Decode(blob)
	if err != nil {
		return 0, err
	}
	s.Sites = c.Sites
	s.Rows = map[int64]CameraRow{}
	for _, r := range c.Cameras {
		s.Rows[r.ID] = r
	}
	return c.Revision, nil
}

// Edit is "the operator edited a camera": the row changes and the revision
// moves past every other row's, as М10's trigger does.
func (s *FakeClusterStore) Edit(id int64, change func(*CameraRow)) int64 {
	r, ok := s.Rows[id]
	if !ok {
		r = Cam(id, 0)
	}
	change(&r)
	rev, _ := s.ConfigRevision()
	r.Revision = rev + 1
	s.Rows[id] = r
	return r.Revision
}

// Condition is a test helper: the condition as (status, reason).
func (s *FakeClusterStore) Cond(cam int64, cond string) Condition {
	return s.Conditions[[2]string{itoa(cam), cond}]
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }

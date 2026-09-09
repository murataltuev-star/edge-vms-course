package cluster

// The sweep that turns files back into index rows.
//
// Two things produce segment files no index row names:
//
//   - a fenced instance kept writing into its own epoch directory after a
//     replacement took over (Lesson 4) — real footage of the partition minute;
//   - a server came back after a failover with an archive whose index rows
//     are on a database that was rebuilt empty elsewhere (Lesson 3: the index
//     does not travel; it is rebuilt from the segments).
//
// Both are the same sweep: walk the archive, parse <cam>/e<epoch>/<start>Z.mp4,
// insert the rows that are missing, keep the epoch from the path. A segment
// younger than two segment lengths may still be open and is left alone; the
// console shows rows whose epoch is older than the current one as *recorded
// by a fenced instance*. Nothing is deleted here.

import (
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var segmentRe = regexp.MustCompile(`^(\d{8}T\d{6}Z)\.mp4$`)
var epochDirRe = regexp.MustCompile(`^e\d+$`)

type ReindexReport struct {
	Reindexed         int
	Fenced            int // of the reindexed, how many carry an epoch older than the current one
	SkippedOpen       int
	SkippedUnparsable int
	Paths             []string
}

type FileInfo struct {
	Path  string
	Mtime float64 // unix seconds
}

// FS is what the sweep needs of a filesystem; RealFs walks a directory.
type FS interface {
	WalkFiles(root string) []FileInfo
	Size(path string) int64
}

// Parse returns (camera, epoch, start, true) for <archive>/<cam>/e<epoch>/<start>Z.mp4.
func Parse(path, archiveDir string) (cam, epoch int64, start time.Time, ok bool) {
	rel, err := filepath.Rel(archiveDir, path)
	if err != nil {
		return
	}
	parts := strings.Split(rel, string(filepath.Separator))
	if len(parts) != 3 || !epochDirRe.MatchString(parts[1]) {
		return
	}
	if cam, err = strconv.ParseInt(parts[0], 10, 64); err != nil {
		return 0, 0, time.Time{}, false
	}
	m := segmentRe.FindStringSubmatch(parts[2])
	if m == nil {
		return 0, 0, time.Time{}, false
	}
	if start, err = time.Parse("20060102T150405Z", m[1]); err != nil {
		return 0, 0, time.Time{}, false
	}
	epoch, _ = strconv.ParseInt(parts[1][1:], 10, 64)
	return cam, epoch, start.UTC(), true
}

func Sweep(store Store, fsys FS, archiveDir string, currentEpoch int64, segmentSeconds int, now float64) (ReindexReport, error) {
	var rep ReindexReport
	indexed, err := store.IndexedPathsUnder(archiveDir)
	if err != nil {
		return rep, err
	}
	grace := float64(2 * segmentSeconds)
	for _, f := range fsys.WalkFiles(archiveDir) {
		if indexed[f.Path] {
			continue
		}
		cam, epoch, start, ok := Parse(f.Path, archiveDir)
		if !ok {
			rep.SkippedUnparsable++
			continue
		}
		if now-f.Mtime < grace {
			rep.SkippedOpen++ // may still be being written
			continue
		}
		end := time.Unix(0, int64(f.Mtime*1e9)).UTC()
		if !end.After(start) {
			rep.SkippedUnparsable++
			continue
		}
		if err := store.IndexSegment(Segment{cam, start, end, f.Path, fsys.Size(f.Path), epoch}); err != nil {
			return rep, err
		}
		rep.Reindexed++
		rep.Paths = append(rep.Paths, f.Path)
		if epoch < currentEpoch {
			rep.Fenced++
		}
	}
	if rep.Reindexed > 0 {
		err = store.LogEvent("archive.reindexed", nil,
			map[string]any{"segments": rep.Reindexed, "fenced": rep.Fenced, "current_epoch": currentEpoch})
		log.Printf("reindexed %d segments (%d from fenced epochs)", rep.Reindexed, rep.Fenced)
	}
	return rep, err
}

type RealFs struct{}

func (RealFs) WalkFiles(root string) []FileInfo {
	var out []FileInfo
	filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		if info, err := d.Info(); err == nil {
			out = append(out, FileInfo{p, float64(info.ModTime().UnixNano()) / 1e9})
		}
		return nil
	})
	return out
}

func (RealFs) Size(path string) int64 {
	st, err := os.Stat(path)
	if err != nil {
		return 0
	}
	return st.Size()
}

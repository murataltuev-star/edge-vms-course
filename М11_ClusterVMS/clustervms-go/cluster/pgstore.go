//go:build pg

package cluster

// PgStore is М10's PgStore in Go on pgx — the same statements, the same
// migrations directory, the same three ClusterVMS additions. Behind the
// `pg` build tag because pgx is the one dependency this project has:
//
//	go get github.com/jackc/pgx/v5
//	go build -tags pg ./cmd/clustervms
//
// Type-checked against pgx's API on the machine this was written on (the
// module proxy was not reachable there, so it was compiled against a stub
// carrying pgx's signatures, not run against Postgres). The SQL is the
// Python version's, which М10 verified on Postgres 16.

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const cameraColumns = "id, site_id, name, rtsp_url, cred_username, cred_secret, enabled, retention_days, priority, revision"

type PgStore struct {
	Pool *pgxpool.Pool
	ctx  context.Context
}

func ConnectPg(ctx context.Context, dsn string) (*PgStore, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, err
	}
	return &PgStore{Pool: pool, ctx: ctx}, nil
}

func (s *PgStore) Close() { s.Pool.Close() }

var migrationRe = regexp.MustCompile(`^(\d+)_.*\.sql$`)

// Migrate is idempotent, one transaction per file, and never leaves the box
// unbootable: a failing migration is logged and the host carries on with
// the schema it has.
func (s *PgStore) Migrate(dir string) (bool, error) {
	if _, err := s.Pool.Exec(s.ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (
		version int PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return false, err
	}
	applied := map[int]bool{}
	rows, err := s.Pool.Query(s.ctx, "SELECT version FROM schema_migrations")
	if err != nil {
		return false, err
	}
	for rows.Next() {
		var v int
		if err := rows.Scan(&v); err != nil {
			rows.Close()
			return false, err
		}
		applied[v] = true
	}
	rows.Close()
	entries, err := os.ReadDir(dir)
	if err != nil {
		return false, err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	for _, name := range names {
		m := migrationRe.FindStringSubmatch(name)
		if m == nil {
			continue
		}
		version, _ := strconv.Atoi(m[1])
		if applied[version] {
			continue
		}
		sql, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			return false, err
		}
		tx, err := s.Pool.Begin(s.ctx)
		if err != nil {
			return false, err
		}
		if _, err = tx.Exec(s.ctx, string(sql)); err == nil {
			_, err = tx.Exec(s.ctx, "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)", version, name)
		}
		if err != nil {
			tx.Rollback(s.ctx)
			log.Printf("migration %s FAILED; continuing on the previous schema: %v", name, err)
			return false, nil
		}
		if err := tx.Commit(s.ctx); err != nil {
			return false, err
		}
		log.Printf("migration %s applied", name)
	}
	return true, nil
}

func (s *PgStore) scanCameras(rows pgx.Rows) ([]CameraRow, error) {
	defer rows.Close()
	var out []CameraRow
	for rows.Next() {
		var c CameraRow
		if err := rows.Scan(&c.ID, &c.SiteID, &c.Name, &c.RtspURL, &c.CredUsername, &c.CredSecret,
			&c.Enabled, &c.RetentionDays, &c.Priority, &c.Revision); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

func (s *PgStore) FetchDesired() ([]CameraRow, error) {
	rows, err := s.Pool.Query(s.ctx, "SELECT "+cameraColumns+" FROM cameras ORDER BY id")
	if err != nil {
		return nil, err
	}
	return s.scanCameras(rows)
}

func (s *PgStore) Cameras() ([]CameraRow, error) { return s.FetchDesired() }

func (s *PgStore) Report(rows []StatusRow) error {
	batch := &pgx.Batch{}
	for _, r := range rows {
		batch.Queue("UPDATE cameras SET observed_revision=$2, phase=$3, last_seen=now() WHERE id=$1",
			r.Camera, r.ObservedRevision, r.Phase)
	}
	return s.Pool.SendBatch(s.ctx, batch).Close()
}

// SetCondition: `since` moves only when the status flips — "storage
// unavailable since 14:02" is the sentence that turns a ticket into a fix.
func (s *PgStore) SetCondition(camera int64, condition string, status bool, reason string) error {
	var r *string
	if reason != "" {
		r = &reason
	}
	_, err := s.Pool.Exec(s.ctx, `
		INSERT INTO camera_conditions (camera_id, condition, status, reason)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (camera_id, condition) DO UPDATE
		   SET reason = EXCLUDED.reason,
		       since  = CASE WHEN camera_conditions.status = EXCLUDED.status
		                     THEN camera_conditions.since ELSE now() END,
		       status = EXCLUDED.status`, camera, condition, status, r)
	return err
}

func (s *PgStore) IndexSegment(seg Segment) error {
	_, err := s.Pool.Exec(s.ctx, "INSERT INTO segments (camera_id, span, path, bytes, epoch) "+
		"VALUES ($1, tstzrange($2, $3, '[)'), $4, $5, $6)", seg.Camera, seg.Start, seg.End, seg.Path, seg.Size, seg.Epoch)
	return err
}

func (s *PgStore) IndexedPathsUnder(prefix string) (map[string]bool, error) {
	rows, err := s.Pool.Query(s.ctx, "SELECT path FROM segments WHERE path LIKE $1", prefix+"%")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]bool{}
	for rows.Next() {
		var p string
		if err := rows.Scan(&p); err != nil {
			return nil, err
		}
		out[p] = true
	}
	return out, rows.Err()
}

func (s *PgStore) LogEvent(kind string, camera *int64, payload map[string]any) error {
	if payload == nil {
		payload = map[string]any{}
	}
	b, _ := json.Marshal(payload)
	_, err := s.Pool.Exec(s.ctx, "INSERT INTO events (camera_id, kind, payload) VALUES ($1, $2, $3::jsonb)", camera, kind, string(b))
	return err
}

// -- the three ClusterVMS statements ---------------------------------------

func (s *PgStore) DumpConfig() ([]byte, error) {
	cams, err := s.FetchDesired()
	if err != nil {
		return nil, err
	}
	var sites []Site
	rows, err := s.Pool.Query(s.ctx, "SELECT id, name FROM sites ORDER BY id")
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var st Site
		if err := rows.Scan(&st.ID, &st.Name); err != nil {
			rows.Close()
			return nil, err
		}
		sites = append(sites, st)
	}
	rows.Close()
	ops, err := s.rowsAsJSON("SELECT row_to_json(o) FROM (SELECT id, username, pwhash FROM operators ORDER BY id) o")
	if err != nil {
		return nil, err
	}
	grants, err := s.rowsAsJSON("SELECT row_to_json(g) FROM (SELECT id, subject, capability, valid_until FROM grants ORDER BY id) g")
	if err != nil {
		return nil, err
	}
	return Encode(sites, cams, ops, grants)
}

func (s *PgStore) rowsAsJSON(sql string) ([]json.RawMessage, error) {
	rows, err := s.Pool.Query(s.ctx, sql)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []json.RawMessage
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		out = append(out, json.RawMessage(raw))
	}
	return out, rows.Err()
}

func (s *PgStore) ConfigRevision() (int64, error) {
	var rev int64
	err := s.Pool.QueryRow(s.ctx, "SELECT COALESCE(MAX(revision), 0) FROM cameras").Scan(&rev)
	return rev, err
}

func (s *PgStore) IsUnconfigured() (bool, error) {
	var n int64
	err := s.Pool.QueryRow(s.ctx, "SELECT count(*) FROM cameras").Scan(&n)
	return n == 0, err
}

// RestoreConfig is step 4 of the restore. Rows keep their ids and
// revisions; the sequences are moved past them; the revision trigger is not
// fired by INSERT, so revision comes back exactly as published.
func (s *PgStore) RestoreConfig(blob []byte) (int64, error) {
	c, err := Decode(blob)
	if err != nil {
		return 0, err
	}
	tx, err := s.Pool.Begin(s.ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(s.ctx)
	for _, st := range c.Sites {
		if _, err := tx.Exec(s.ctx, "INSERT INTO sites (id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING", st.ID, st.Name); err != nil {
			return 0, err
		}
	}
	for _, cam := range c.Cameras {
		if _, err := tx.Exec(s.ctx, "INSERT INTO cameras ("+cameraColumns+") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT (id) DO NOTHING",
			cam.ID, cam.SiteID, cam.Name, cam.RtspURL, cam.CredUsername, cam.CredSecret, cam.Enabled, cam.RetentionDays, cam.Priority, cam.Revision); err != nil {
			return 0, err
		}
	}
	for _, raw := range c.Operators {
		var o struct {
			ID       int64  `json:"id"`
			Username string `json:"username"`
			Pwhash   string `json:"pwhash"`
		}
		if err := json.Unmarshal(raw, &o); err != nil {
			return 0, err
		}
		if _, err := tx.Exec(s.ctx, "INSERT INTO operators (id, username, pwhash) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING", o.ID, o.Username, o.Pwhash); err != nil {
			return 0, err
		}
	}
	for _, raw := range c.Grants {
		var g struct {
			ID         int64   `json:"id"`
			Subject    string  `json:"subject"`
			Capability string  `json:"capability"`
			ValidUntil *string `json:"valid_until"`
		}
		if err := json.Unmarshal(raw, &g); err != nil {
			return 0, err
		}
		if _, err := tx.Exec(s.ctx, "INSERT INTO grants (id, subject, capability, valid_until) VALUES ($1, $2, $3, $4::timestamptz) ON CONFLICT (id) DO NOTHING",
			g.ID, g.Subject, g.Capability, g.ValidUntil); err != nil {
			return 0, err
		}
	}
	for _, table := range []string{"cameras", "operators", "grants"} {
		if _, err := tx.Exec(s.ctx, fmt.Sprintf("SELECT setval(pg_get_serial_sequence('%s', 'id'), COALESCE((SELECT MAX(id) FROM %s), 1))", table, table)); err != nil {
			return 0, err
		}
	}
	return c.Revision, tx.Commit(s.ctx)
}

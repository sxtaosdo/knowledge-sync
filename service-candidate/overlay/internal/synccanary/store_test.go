package synccanary

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func must(t *testing.T, e error) {
	t.Helper()
	if e != nil {
		t.Fatal(e)
	}
}
func synthetic(t *testing.T) (*gorm.DB, Policy, string) {
	t.Helper()
	db, e := gorm.Open(sqlite.Open("file:"+uuid.NewString()+"?mode=memory&cache=shared&_busy_timeout=5000"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	must(t, e)
	sqlDB, e := db.DB()
	must(t, e)
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = sqlDB.Close() })
	// Application migrations occur only in this disposable synthetic fixture.
	must(t, db.AutoMigrate(&types.KnowledgeBase{}, &types.Knowledge{}, &types.Chunk{}))
	must(t, db.Exec(`CREATE TABLE lite_embeddings(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at DATETIME,updated_at DATETIME,source_id TEXT NOT NULL,source_type INTEGER NOT NULL,chunk_id TEXT,knowledge_id TEXT,knowledge_base_id TEXT,tag_id TEXT,content TEXT NOT NULL,dimension INTEGER NOT NULL,is_enabled INTEGER,UNIQUE(source_id,source_type))`).Error)
	must(t, db.Exec(`CREATE VIRTUAL TABLE lite_embeddings_fts USING fts5(content,source_id,chunk_id,knowledge_id,knowledge_base_id,content='',contentless_delete=1,tokenize='unicode61')`).Error)
	content := "# Scope\n\n状态: Accepted\n\n固定父版本 canary keyword.\n"
	p := Policy{SchemaVersion: 1, InstanceID: uuid.NewString(), ExpiresAt: time.Now().Add(time.Hour).Unix()}
	for i, ch := range []string{"local", "official"} {
		name := []string{"shidai3-dev-canary", "shidai3-official-canary"}[i]
		kbID := uuid.NewString()
		kb := types.KnowledgeBase{ID: kbID, TenantID: 1, Name: name, Type: types.KnowledgeBaseTypeDocument, IndexingStrategy: types.IndexingStrategy{KeywordEnabled: true}}
		must(t, db.Create(&kb).Error)
		target := Target{TenantID: 1, KBID: kbID, Name: name, Channel: ch}
		for _, path := range []string{"AGENTS.md", "AI_CONTEXT.md"} {
			op := hashBytes([]byte(ch + path))
			desired := map[string]any{"owner": "fixture-owner", "repo_id": "shidai3", "channel": ch, "tenant_id": "1", "kb_id": kbID, "path": path, "source_key": "shidai3:" + ch + ":" + path, "source_commit": strings.Repeat("a", 40), "parent_commit": strings.Repeat("a", 40), "blob_sha": strings.Repeat("b", 40), "title": "Scope", "operation_id": op}
			target.Documents = append(target.Documents, ApprovedDocument{hashBytes([]byte(content)), desired})
		}
		p.Targets = append(p.Targets, target)
	}
	return db, p, content
}
func enable(t *testing.T, db *gorm.DB, p Policy) *Store {
	t.Helper()
	path := filepath.Join(t.TempDir(), "policy.json")
	b, e := encoded(p)
	must(t, e)
	must(t, os.WriteFile(path, b, 0600))
	t.Setenv("WEKNORA_SYNC_CANARY_POLICY", path)
	s, e := New(db)
	must(t, e)
	if !s.Enabled() {
		t.Fatal("not enabled")
	}
	return s
}
func request(p Policy, content string) Request {
	a := p.Targets[0].Documents[0]
	return Request{OperationID: text(a.Desired, "operation_id"), Kind: "add", Desired: a.Desired, Content: content}
}
func queued(t *testing.T, s *Store, r Request) job {
	t.Helper()
	b, e := encoded(r)
	must(t, e)
	target := s.policy.Targets[0]
	j := job{OperationID: r.OperationID, InstanceID: s.policy.InstanceID, TenantID: target.TenantID, KBID: target.KBID, SourceKey: text(r.Desired, "source_key"), Payload: string(b), PayloadHash: hashBytes(b), DocumentID: uuid.NewString(), ChunkID: uuid.NewString(), TaskID: uuid.NewString(), State: "queued"}
	must(t, s.db.Create(&j).Error)
	return j
}
func ready(t *testing.T, s *Store, r Request) Observation {
	t.Helper()
	target := s.policy.Targets[0]
	result, e := s.Accept(context.Background(), target.TenantID, target.KBID, r)
	must(t, e)
	must(t, s.Process(context.Background(), result.OperationID))
	obs, e := s.Observe(context.Background(), target.TenantID, target.KBID, r.OperationID)
	must(t, e)
	if obs.Proof["state"] != "ready" {
		t.Fatalf("not ready: %+v", obs.Proof)
	}
	return obs
}
func count(t *testing.T, db *gorm.DB, table string) int64 {
	t.Helper()
	var n int64
	must(t, db.Table(table).Count(&n).Error)
	return n
}

func TestDefaultDisabledTouchesNoDB(t *testing.T) {
	t.Setenv("WEKNORA_SYNC_CANARY_POLICY", "")
	s, e := New(nil)
	must(t, e)
	if s.Enabled() {
		t.Fatal("disabled default enabled")
	}
	if e = s.Resume(); e != ErrDisabled {
		t.Fatalf("expected disabled, got %v", e)
	}
}
func TestAcceptPositiveInventoryIdempotent(t *testing.T) {
	db, p, c := synthetic(t)
	s := enable(t, db, p)
	r := request(p, c)
	obs := ready(t, s, r)
	if obs.Operation.DocumentID != obs.Document.ID || obs.Operation.TaskID == "" || obs.Proof["taskState"] != "done" || len(obs.Chunks) != 1 {
		t.Fatal("incomplete receipt")
	}
	again, e := s.Accept(context.Background(), 1, p.Targets[0].KBID, r)
	must(t, e)
	if again.DocumentID != obs.Document.ID || again.TaskID != obs.Operation.TaskID || again.State != "done" {
		t.Fatal("duplicate changed identity")
	}
	for _, table := range []string{"knowledges", "chunks", "lite_embeddings", "lite_embeddings_fts", "sync_canary_operations"} {
		if count(t, db, table) != 1 {
			t.Fatal(table, "duplicate row")
		}
	}
	got := s.Policy()
	got.Targets[0].Documents[0].Desired["title"] = "mutable copy"
	if text(s.policy.Targets[0].Documents[0].Desired, "title") != "Scope" {
		t.Fatal("Policy leaked mutable map")
	}
}
func TestCrossScopeAndFrozenPayloadRejectBeforeQueue(t *testing.T) {
	db, p, c := synthetic(t)
	s := enable(t, db, p)
	r := request(p, c)
	for _, scope := range []struct {
		tenant uint64
		kb     string
	}{{2, p.Targets[0].KBID}, {1, p.Targets[1].KBID}, {1, uuid.NewString()}} {
		if _, e := s.Accept(context.Background(), scope.tenant, scope.kb, r); e == nil {
			t.Fatal("crossscope accepted")
		}
	}
	r.Content += "tampered"
	if _, e := s.Accept(context.Background(), 1, p.Targets[0].KBID, r); e == nil {
		t.Fatal("content not frozen")
	}
	r = request(p, c)
	r.Kind = "delete"
	if _, e := s.Accept(context.Background(), 1, p.Targets[0].KBID, r); e != ErrUnsupported {
		t.Fatal("delete not denied", e)
	}
	r.Kind = "update"
	if _, e := s.Accept(context.Background(), 1, p.Targets[0].KBID, r); e != ErrUnsupported {
		t.Fatal("update not denied", e)
	}
	if count(t, db, "sync_canary_operations") != 0 {
		t.Fatal("denial queued work")
	}
}
func TestAtomicFailureRollsBackApplicationRows(t *testing.T) {
	db, p, c := synthetic(t)
	s := enable(t, db, p)
	j := queued(t, s, request(p, c))
	must(t, db.Exec(`DROP TABLE lite_embeddings`).Error)
	if e := s.Process(context.Background(), j.OperationID); e == nil {
		t.Fatal("missing index table accepted")
	}
	if count(t, db, "knowledges") != 0 || count(t, db, "chunks") != 0 || count(t, db, "lite_embeddings_fts") != 0 {
		t.Fatal("partial atomic write escaped")
	}
	obs, e := s.Observe(context.Background(), 1, p.Targets[0].KBID, j.OperationID)
	if e == nil && obs.Proof["state"] != "failed" {
		t.Fatal("failure not durable")
	}
	var failed job
	must(t, db.First(&failed, "operation_id=?", j.OperationID).Error)
	if failed.State != "failed" {
		t.Fatal("rollback failure not durably recorded")
	}
}
func TestPostingsCorruptionAndExtraFTSRowFailClosed(t *testing.T) {
	for _, extra := range []bool{false, true} {
		t.Run(fmt.Sprint(extra), func(t *testing.T) {
			db, p, c := synthetic(t)
			s := enable(t, db, p)
			r := request(p, c)
			obs := ready(t, s, r)
			var emb embedding
			must(t, db.Where("chunk_id=?", obs.Chunks[0].ID).First(&emb).Error)
			rowid := emb.ID
			if extra {
				rowid += 1000
			} else {
				must(t, db.Exec(`DELETE FROM lite_embeddings_fts WHERE rowid=?`, rowid).Error)
			}
			must(t, db.Exec(`INSERT INTO lite_embeddings_fts(rowid,content,source_id,chunk_id,knowledge_id,knowledge_base_id) VALUES(?,?,?,?,?,?)`, rowid, "invented wrong postings", emb.SourceID, emb.ChunkID, emb.KnowledgeID, emb.KnowledgeBaseID).Error)
			bad, e := s.Observe(context.Background(), 1, p.Targets[0].KBID, r.OperationID)
			if e == nil && bad.Proof["state"] != "unknown" {
				t.Fatalf("corrupt postings accepted: %+v", bad.Proof)
			}
		})
	}
}

func TestIdempotencyPayloadHashConflictAndUnownedRuntimeRows(t *testing.T) {
	t.Run("payloadhash", func(t *testing.T) {
		db, p, c := synthetic(t)
		s := enable(t, db, p)
		r := request(p, c)
		j := queued(t, s, r)
		must(t, db.Model(&job{}).Where("operation_id=?", j.OperationID).Update("payload_hash", strings.Repeat("0", 64)).Error)
		if _, e := s.Accept(context.Background(), 1, p.Targets[0].KBID, r); e != ErrConflict {
			t.Fatalf("expected conflict: %v", e)
		}
	})
	t.Run("unowned", func(t *testing.T) {
		db, p, c := synthetic(t)
		s := enable(t, db, p)
		old := types.Knowledge{ID: uuid.NewString(), TenantID: 1, KnowledgeBaseID: p.Targets[0].KBID, CustomMetadata: types.JSON(`{}`)}
		must(t, db.Create(&old).Error)
		if _, e := s.Accept(context.Background(), 1, p.Targets[0].KBID, request(p, c)); e == nil {
			t.Fatal("late historical row accepted")
		}
	})
}
func TestCurrentRowsVersionAndCompleteChunksRequired(t *testing.T) {
	for _, mutation := range []string{"content", "extra_chunk", "metadata", "disable"} {
		t.Run(mutation, func(t *testing.T) {
			db, p, c := synthetic(t)
			s := enable(t, db, p)
			r := request(p, c)
			obs := ready(t, s, r)
			switch mutation {
			case "content":
				must(t, db.Model(&types.Chunk{}).Where("id=?", obs.Chunks[0].ID).Update("content", "wrong").Error)
			case "extra_chunk":
				more := *obs.Chunks[0]
				more.ID = uuid.NewString()
				more.SeqID += 100
				must(t, db.Create(&more).Error)
			case "metadata":
				var m map[string]any
				must(t, json.Unmarshal(obs.Document.Metadata, &m))
				m["version"] = 2
				b, e := encoded(m)
				must(t, e)
				must(t, db.Model(&types.Knowledge{}).Where("id=?", obs.Document.ID).Update("metadata", types.JSON(b)).Error)
			case "disable":
				must(t, db.Model(&types.Knowledge{}).Where("id=?", obs.Document.ID).Update("enable_status", "disabled").Error)
			}
			bad, e := s.Observe(context.Background(), 1, p.Targets[0].KBID, r.OperationID)
			if e == nil && bad.Proof["state"] != "unknown" {
				t.Fatal("changed current rows accepted")
			}
		})
	}
}
func TestQueuedAndRunningResumeOnlyOwned(t *testing.T) {
	for _, state := range []string{"queued", "running"} {
		t.Run(state, func(t *testing.T) {
			db, p, c := synthetic(t)
			s := enable(t, db, p)
			j := queued(t, s, request(p, c))
			must(t, db.Model(&job{}).Where("operation_id=?", j.OperationID).Update("state", state).Error)
			pending, e := s.Observe(context.Background(), 1, p.Targets[0].KBID, j.OperationID)
			must(t, e)
			if pending.Proof["state"] != "pending" || pending.Operation.DocumentID != j.DocumentID || pending.Operation.TaskID != j.TaskID {
				t.Fatal("lost POST identity missing")
			}
			// A historical pending row in a third KB is never selected or processed.
			historical := types.Knowledge{ID: uuid.NewString(), TenantID: 1, KnowledgeBaseID: uuid.NewString(), ParseStatus: "pending", CustomMetadata: types.JSON(`{}`)}
			must(t, db.Create(&historical).Error)
			restarted, e := New(db)
			must(t, e)
			must(t, restarted.Resume())
			must(t, restarted.Process(context.Background(), j.OperationID))
			obs, e := restarted.Observe(context.Background(), 1, p.Targets[0].KBID, j.OperationID)
			must(t, e)
			if obs.Proof["state"] != "ready" {
				t.Fatal("resume did not finish")
			}
			var old types.Knowledge
			must(t, db.First(&old, "id=?", historical.ID).Error)
			if old.ParseStatus != "pending" {
				t.Fatal("historical task touched")
			}
			// Restart again with owned complete rows; original instance remains accepted.
			_, e = New(db)
			must(t, e)
		})
	}
}
func TestWrongPolicyAndHistoricalAdoptionRejected(t *testing.T) {
	t.Run("nonempty", func(t *testing.T) {
		db, p, _ := synthetic(t)
		old := types.Knowledge{ID: uuid.NewString(), TenantID: 1, KnowledgeBaseID: p.Targets[0].KBID, CustomMetadata: types.JSON(`{}`)}
		must(t, db.Create(&old).Error)
		if e := InitSchema(db, p); e == nil {
			t.Fatal("old KB adopted")
		}
		if db.Migrator().HasTable("sync_canary_operations") {
			t.Fatal("denied init persisted schema")
		}
	})
	t.Run("vector", func(t *testing.T) {
		db, p, _ := synthetic(t)
		b, e := encoded(types.IndexingStrategy{KeywordEnabled: true, VectorEnabled: true})
		must(t, e)
		must(t, db.Model(&types.KnowledgeBase{}).Where("id=?", p.Targets[0].KBID).Update("indexing_strategy", string(b)).Error)
		if e = InitSchema(db, p); e == nil {
			t.Fatal("vector enabled policy accepted")
		}
	})
	t.Run("changedfile", func(t *testing.T) {
		db, p, c := synthetic(t)
		s := enable(t, db, p)
		p.ExpiresAt++
		b, e := encoded(p)
		must(t, e)
		must(t, os.WriteFile(s.policyPath, b, 0600))
		if _, e = s.Accept(context.Background(), 1, p.Targets[0].KBID, request(p, c)); e == nil {
			t.Fatal("changed frozen policy accepted")
		}
	})
	t.Run("wronginstance", func(t *testing.T) {
		db, p, _ := synthetic(t)
		_ = enable(t, db, p)
		p.InstanceID = uuid.NewString()
		if e := InitSchema(db, p); e == nil {
			t.Fatal("new instance adopted same KB")
		}
	})
	t.Run("expiry", func(t *testing.T) {
		db, p, _ := synthetic(t)
		p.ExpiresAt = time.Now().Unix() - 1
		if e := InitSchema(db, p); e == nil {
			t.Fatal("expired policy accepted")
		}
	})
	t.Run("duplicatekeys", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), "policy.json")
		must(t, os.WriteFile(path, []byte(`{"schemaVersion":1,"schemaVersion":1}`), 0600))
		if _, e := readPolicy(path); e == nil {
			t.Fatal("duplicate policy keys accepted")
		}
	})
}
func TestPinnedTokenizer(t *testing.T) {
	if got := tokenizeCJKBigram("你好世界 C++ 接口"); got != "你好 好世 世界 C 接口" {
		t.Fatal(got)
	}
}

func TestObserveIsReadOnlyAndDoesNotRepairMissingVocabulary(t *testing.T) {
	db, p, c := synthetic(t)
	s := enable(t, db, p)
	r := request(p, c)
	_ = ready(t, s, r)
	must(t, db.Exec(`PRAGMA query_only=ON`).Error)
	observation, e := s.Observe(context.Background(), 1, p.Targets[0].KBID, r.OperationID)
	must(t, e)
	if observation.Proof["state"] != "ready" {
		t.Fatal("readonly observation unavailable")
	}
	must(t, db.Exec(`PRAGMA query_only=OFF`).Error)
	must(t, db.Exec(`DROP TABLE lite_embeddings_fts_vocab`).Error)
	must(t, db.Exec(`PRAGMA query_only=ON`).Error)
	if _, e = s.Observe(context.Background(), 1, p.Targets[0].KBID, r.OperationID); e != ErrInventory {
		t.Fatalf("missing vocabulary did not fail closed: %v", e)
	}
	var n int64
	must(t, db.Raw(`SELECT count(*) FROM sqlite_master WHERE name='lite_embeddings_fts_vocab'`).Scan(&n).Error)
	if n != 0 {
		t.Fatal("readonly observer repaired schema")
	}
	must(t, db.Exec(`PRAGMA query_only=OFF`).Error)
}

package sqlite

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	gormsqlite "gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

type existingConstructorTrace struct {
	logger.Interface
	statements []string
}

func (l *existingConstructorTrace) Trace(_ context.Context, _ time.Time, query func() (string, int64), _ error) {
	sql, _ := query()
	l.statements = append(l.statements, sql)
}
func constructorMust(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
func constructorDB(t *testing.T) (*gorm.DB, *existingConstructorTrace) {
	t.Helper()
	trace := &existingConstructorTrace{Interface: logger.Default.LogMode(logger.Silent)}
	db, e := gorm.Open(gormsqlite.Open("file:"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: trace})
	constructorMust(t, e)
	sqlDB, e := db.DB()
	constructorMust(t, e)
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = sqlDB.Close() })
	return db, trace
}

const existingMetadataDDL = `CREATE TABLE lite_embeddings(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at datetime,updated_at datetime,source_id TEXT NOT NULL,source_type INTEGER NOT NULL,chunk_id TEXT,knowledge_id TEXT,knowledge_base_id TEXT,tag_id TEXT,content TEXT NOT NULL,dimension INTEGER NOT NULL,is_enabled numeric)`
const existingFTSDDL = `CREATE VIRTUAL TABLE lite_embeddings_fts USING fts5(content,source_id,chunk_id,knowledge_id,knowledge_base_id,content='',contentless_delete=1,tokenize='unicode61')`

func constructorFixture(t *testing.T, db *gorm.DB) {
	t.Helper()
	constructorMust(t, db.Exec(existingMetadataDDL).Error)
	constructorMust(t, db.Exec(existingFTSDDL).Error)
	constructorMust(t, db.Exec(`INSERT INTO lite_embeddings(id,source_id,source_type,chunk_id,knowledge_id,knowledge_base_id,tag_id,content,dimension,is_enabled) VALUES(41,'legacy-source',0,'legacy-chunk','legacy-doc','legacy-kb','','历史 keyword sentinel',777,1)`).Error)
	constructorMust(t, db.Exec(`INSERT INTO lite_embeddings_fts(rowid,content,source_id,chunk_id,knowledge_id,knowledge_base_id) VALUES(41,?,'legacy-source','legacy-chunk','legacy-doc','legacy-kb')`, tokenizeCJKBigram("历史 keyword sentinel")).Error)
	constructorMust(t, db.Exec(`CREATE VIRTUAL TABLE constructor_test_vocab USING fts5vocab(lite_embeddings_fts,'instance')`).Error)
}
func constructorSnapshot(t *testing.T, db *gorm.DB) string {
	t.Helper()
	snapshot := map[string]any{}
	for name, query := range map[string]string{"schema": `SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name`, "metadata": `SELECT * FROM lite_embeddings ORDER BY id`, "fts": `SELECT rowid FROM lite_embeddings_fts ORDER BY rowid`, "postings": `SELECT term,doc,col,offset FROM constructor_test_vocab ORDER BY doc,col,offset,term`} {
		var rows []map[string]any
		constructorMust(t, db.Raw(query).Scan(&rows).Error)
		snapshot[name] = rows
	}
	b, e := json.Marshal(snapshot)
	constructorMust(t, e)
	return string(b)
}

func TestSyncCanaryExistingConstructorReadOnlyAndPreservesLegacyIndexes(t *testing.T) {
	db, trace := constructorDB(t)
	constructorFixture(t, db)
	before := constructorSnapshot(t, db)
	constructorMust(t, db.Exec(`PRAGMA query_only=ON`).Error)
	trace.statements = nil
	result, e := NewExistingSQLiteRetrieveEngineRepository(db)
	constructorMust(t, e)
	repository, ok := result.(*sqliteRepository)
	if !ok || !repository.existingSchemaOnly {
		t.Fatal("existing-only path missing")
	}
	if repository.vecTables[777] {
		t.Fatal("legacy dimension became a vector table")
	}
	for _, statement := range trace.statements {
		first := strings.ToUpper(strings.Fields(statement)[0])
		if first != "SELECT" && first != "PRAGMA" {
			t.Fatalf("constructor attempted mutation: %s", statement)
		}
	}
	if after := constructorSnapshot(t, db); before != after {
		t.Fatal("constructor changed old rows, schema, FTS or postings")
	}
	// Even a later vector query may not invoke lazy CREATE on this instance.
	trace.statements = nil
	_, e = repository.Retrieve(context.Background(), types.RetrieveParams{RetrieverType: types.VectorRetrieverType, Embedding: []float32{1, 2, 3}})
	if e == nil || !strings.Contains(e.Error(), "vector_table_unavailable") {
		t.Fatal("missing existing vector table did not fail closed", e)
	}
	repository.ensureVecTable(777)
	for _, statement := range trace.statements {
		if strings.HasPrefix(strings.ToUpper(strings.TrimSpace(statement)), "CREATE") {
			t.Fatal("existing-only instance lazily created schema")
		}
	}
	if after := constructorSnapshot(t, db); before != after {
		t.Fatal("lazy path changed old state")
	}
	constructorMust(t, db.Exec(`PRAGMA query_only=OFF`).Error)
}

func TestSyncCanaryExistingConstructorRejectsMissingOrIncompatibleSchemas(t *testing.T) {
	cases := []struct{ name, metadata, fts string }{
		{"missing both", "", ""},
		{"missing FTS", existingMetadataDDL, ""},
		{"missing metadata", "", existingFTSDDL},
		{"legacy external content", existingMetadataDDL, `CREATE VIRTUAL TABLE lite_embeddings_fts USING fts5(content,source_id,chunk_id,knowledge_id,knowledge_base_id,content='lite_embeddings',tokenize='unicode61')`},
		{"four columns", existingMetadataDDL, `CREATE VIRTUAL TABLE lite_embeddings_fts USING fts5(content,source_id,chunk_id,knowledge_id,content='',contentless_delete=1,tokenize='unicode61')`},
		{"wrong tokenizer", existingMetadataDDL, `CREATE VIRTUAL TABLE lite_embeddings_fts USING fts5(content,source_id,chunk_id,knowledge_id,knowledge_base_id,content='',contentless_delete=1,tokenize='porter')`},
		{"missing metadata column", strings.Replace(existingMetadataDDL, "tag_id TEXT,", "", 1), existingFTSDDL},
		{"wrong metadata affinity", strings.Replace(existingMetadataDDL, "source_id TEXT", "source_id BLOB", 1), existingFTSDDL},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			db, trace := constructorDB(t)
			if tc.metadata != "" {
				constructorMust(t, db.Exec(tc.metadata).Error)
			}
			if tc.fts != "" {
				constructorMust(t, db.Exec(tc.fts).Error)
			}
			var before []map[string]any
			constructorMust(t, db.Raw(`SELECT name,sql FROM sqlite_master ORDER BY name`).Scan(&before).Error)
			constructorMust(t, db.Exec(`PRAGMA query_only=ON`).Error)
			trace.statements = nil
			result, e := NewExistingSQLiteRetrieveEngineRepository(db)
			if e == nil || result != nil {
				t.Fatal("incompatible schema accepted")
			}
			for _, statement := range trace.statements {
				first := strings.ToUpper(strings.Fields(statement)[0])
				if first != "SELECT" && first != "PRAGMA" {
					t.Fatalf("failed constructor attempted mutation: %s", statement)
				}
			}
			var after []map[string]any
			constructorMust(t, db.Raw(`SELECT name,sql FROM sqlite_master ORDER BY name`).Scan(&after).Error)
			if fmt.Sprint(before) != fmt.Sprint(after) {
				t.Fatal("failed constructor modified schema")
			}
			constructorMust(t, db.Exec(`PRAGMA query_only=OFF`).Error)
		})
	}
}

func TestSyncCanaryExistingConstructorNilDatabaseRejected(t *testing.T) {
	if repository, e := NewExistingSQLiteRetrieveEngineRepository(nil); e == nil || repository != nil {
		t.Fatal("nil DB accepted")
	}
}

// sync-canary-fixture is an offline JSON-lines harness. It accepts no database
// path, credentials, service configuration, network address, or external input source.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/Tencent/WeKnora/internal/synccanary"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

type request struct {
	Method string          `json:"method"`
	Path   string          `json:"path"`
	Body   json.RawMessage `json:"body"`
}
type fixture struct {
	db        *gorm.DB
	store     *synccanary.Store
	directory string
}

func (f *fixture) init(body json.RawMessage) error {
	if f.db != nil {
		return errors.New("fixture_already_initialized")
	}
	var init struct {
		Policy synccanary.Policy `json:"policy"`
	}
	if e := json.Unmarshal(body, &init); e != nil {
		return e
	}
	// Hard fixture boundary: only the two synthetic channels, tenant1 and repo shidai3.
	if len(init.Policy.Targets) != 2 {
		return errors.New("fixture_synthetic_policy_required")
	}
	for _, t := range init.Policy.Targets {
		if t.TenantID != 1 {
			return errors.New("fixture_tenant1_required")
		}
		for _, d := range t.Documents {
			if d.Desired["repo_id"] != "shidai3" {
				return errors.New("fixture_synthetic_source_required")
			}
		}
	}
	db, e := gorm.Open(sqlite.Open("file:"+uuid.NewString()+"?mode=memory&cache=shared&_busy_timeout=5000"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if e != nil {
		return e
	}
	f.db = db
	conn, e := db.DB()
	if e != nil {
		return e
	}
	conn.SetMaxOpenConns(1)
	// Native migrations are confined to this freshly allocated in-memory fixture.
	if e = db.AutoMigrate(&types.KnowledgeBase{}, &types.Knowledge{}, &types.Chunk{}); e != nil {
		return e
	}
	for _, q := range []string{
		`CREATE TABLE lite_embeddings(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at DATETIME,updated_at DATETIME,source_id TEXT NOT NULL,source_type INTEGER NOT NULL,chunk_id TEXT,knowledge_id TEXT,knowledge_base_id TEXT,tag_id TEXT,content TEXT NOT NULL,dimension INTEGER NOT NULL,is_enabled INTEGER,UNIQUE(source_id,source_type))`,
		`CREATE VIRTUAL TABLE lite_embeddings_fts USING fts5(content,source_id,chunk_id,knowledge_id,knowledge_base_id,content='',contentless_delete=1,tokenize='unicode61')`,
	} {
		if e = db.Exec(q).Error; e != nil {
			return e
		}
	}
	for _, t := range init.Policy.Targets {
		kb := types.KnowledgeBase{ID: t.KBID, TenantID: t.TenantID, Name: t.Name, Type: types.KnowledgeBaseTypeDocument, IndexingStrategy: types.IndexingStrategy{KeywordEnabled: true}}
		if e = db.Create(&kb).Error; e != nil {
			return e
		}
	}
	directory, e := os.MkdirTemp("", "sync-canary-synthetic-fixture-")
	if e != nil {
		return e
	}
	f.directory = directory
	path := filepath.Join(directory, "policy.json")
	b, e := json.Marshal(init.Policy)
	if e != nil {
		return e
	}
	if e = os.WriteFile(path, b, 0600); e != nil {
		return e
	}
	if e = os.Setenv("WEKNORA_SYNC_CANARY_POLICY", path); e != nil {
		return e
	}
	f.store, e = synccanary.New(db)
	return e
}
func (f *fixture) close() {
	if f.db != nil {
		if db, e := f.db.DB(); e == nil {
			_ = db.Close()
		}
	}
	if f.directory != "" {
		_ = os.Remove(filepath.Join(f.directory, "policy.json"))
		_ = os.Remove(f.directory)
	}
}
func success(data any) map[string]any {
	return map[string]any{"success": true, "data": data, "status": 200}
}
func failure(status int, code string) map[string]any {
	return map[string]any{"success": false, "status": status, "error": code}
}
func (f *fixture) handle(r request) map[string]any {
	if r.Method == "INIT" {
		if e := f.init(r.Body); e != nil {
			return failure(400, e.Error())
		}
		return success(map[string]any{"fixture": "in-memory-only", "enabled": f.store.Enabled(), "providerId": synccanary.ProviderID})
	}
	if f.store == nil {
		return failure(403, "fixture_not_initialized")
	}
	u, e := url.Parse(r.Path)
	if e != nil {
		return failure(400, "invalid_path")
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if len(parts) < 4 || parts[0] != "api" || parts[1] != "v1" {
		return failure(404, "not_found")
	}
	ctx := context.Background()
	if parts[2] == "knowledge" && len(parts) == 4 && r.Method == "GET" {
		var doc types.Knowledge
		if e = f.db.Where("id=? AND tenant_id=1", parts[3]).First(&doc).Error; e != nil {
			return failure(404, "not_found")
		}
		return success(&doc)
	}
	if parts[2] != "knowledge-bases" {
		return failure(404, "not_found")
	}
	kbID := parts[3]
	var target *synccanary.Target
	for _, t := range f.store.Policy().Targets {
		if t.KBID == kbID {
			copy := t
			target = &copy
		}
	}
	if target == nil {
		return failure(403, "scope_denied")
	}
	if len(parts) == 4 && r.Method == "GET" {
		var kb types.KnowledgeBase
		if e = f.db.First(&kb, "id=?", kbID).Error; e != nil {
			return failure(404, "not_found")
		}
		return success(kb)
	}
	if len(parts) == 5 && parts[4] == "knowledge" && r.Method == "GET" {
		var docs []*types.Knowledge
		if e = f.db.Where("tenant_id=1 AND knowledge_base_id=?", kbID).Order("id").Find(&docs).Error; e != nil {
			return failure(500, "read_failed")
		}
		out := success(docs)
		out["total"] = len(docs)
		return out
	}
	if len(parts) == 5 && parts[4] == "hybrid-search" && r.Method == "POST" {
		var body struct {
			Query string `json:"query"`
		}
		if json.Unmarshal(r.Body, &body) != nil || strings.TrimSpace(body.Query) == "" {
			return failure(400, "query_required")
		}
		// ASCII fixture query is quoted; this is actual FTS retrieval and invokes no model.
		if !strings.HasPrefix(strings.ToLower(body.Query), "synthetic") {
			return failure(400, "synthetic_query_required")
		}
		match := `content:"` + strings.ReplaceAll(body.Query, `"`, `""`) + `" AND knowledge_base_id:"` + kbID + `"`
		var rows []map[string]any
		if e = f.db.Raw(`SELECT e.chunk_id,e.knowledge_id,e.content FROM lite_embeddings e JOIN lite_embeddings_fts f ON f.rowid=e.id WHERE lite_embeddings_fts MATCH ? AND e.knowledge_base_id=? AND e.is_enabled=1 ORDER BY e.id`, match, kbID).Scan(&rows).Error; e != nil {
			return failure(500, "fts_query_failed")
		}
		return success(map[string]any{"results": rows, "total": len(rows), "keywordOnly": true, "modelCalls": 0})
	}
	if len(parts) < 6 || parts[4] != "sync-canary" {
		return failure(404, "not_found")
	}
	if len(parts) == 6 && parts[5] == "capabilities" && r.Method == "GET" {
		return success(map[string]any{"enabled": f.store.Enabled(), "providerId": synccanary.ProviderID, "tenantId": target.TenantID, "kbId": kbID, "channel": target.Channel, "expiresAt": f.store.Policy().ExpiresAt, "documentPaths": []string{"AGENTS.md", "AI_CONTEXT.md"}, "keywordOnly": true, "deleteAllowed": false, "firstAddsOnly": true})
	}
	if len(parts) == 6 && parts[5] == "operations" && r.Method == "POST" {
		var request synccanary.Request
		if e = json.Unmarshal(r.Body, &request); e != nil {
			return failure(400, "invalid_request")
		}
		result, e := f.store.Accept(ctx, target.TenantID, kbID, request)
		if e != nil {
			return failure(403, e.Error())
		}
		return success(result)
	}
	if len(parts) == 7 && parts[5] == "operations" && r.Method == "GET" {
		observation, e := f.store.Observe(ctx, target.TenantID, kbID, parts[6])
		if e != nil {
			return failure(409, e.Error())
		}
		return success(observation)
	}
	return failure(404, "not_found")
}
func main() {
	f := &fixture{}
	defer f.close()
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 65536), 1024*1024)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()
	for scanner.Scan() {
		var r request
		out := failure(400, "invalid_json")
		if json.Unmarshal(scanner.Bytes(), &r) == nil {
			out = f.handle(r)
		}
		b, e := json.Marshal(out)
		if e != nil {
			b = []byte(`{"success":false,"status":500,"error":"serialization_failed"}`)
		}
		if _, e = fmt.Fprintln(writer, string(b)); e != nil {
			return
		}
		if writer.Flush() != nil {
			return
		}
	}
}

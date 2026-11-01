// Package synccanary implements a deliberately bounded, model-free keyword canary.
// It never opens a database, migrates application tables, or runs historical jobs.
package synccanary

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const ProviderID = "native-keyword-canary-v1"

var (
	ErrDisabled      = errors.New("sync_canary_disabled")
	ErrDenied        = errors.New("sync_canary_scope_denied")
	ErrConflict      = errors.New("sync_canary_operation_conflict")
	ErrUnsupported   = errors.New("sync_canary_first_add_only")
	ErrInventory     = errors.New("sync_canary_inventory_unverified")
	operationPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$`)
	hashPattern      = regexp.MustCompile(`^[0-9a-f]{64}$`)
	gitHashPattern   = regexp.MustCompile(`^(?:[0-9a-f]{40}|[0-9a-f]{64})$`)
)

type ApprovedDocument struct {
	ContentSHA256 string         `json:"contentSHA256"`
	Desired       map[string]any `json:"desired"`
}
type Target struct {
	TenantID  uint64             `json:"tenantId"`
	KBID      string             `json:"kbId"`
	Name      string             `json:"name"`
	Channel   string             `json:"channel"`
	Documents []ApprovedDocument `json:"documents"`
}
type Policy struct {
	SchemaVersion int      `json:"schemaVersion"`
	InstanceID    string   `json:"instanceId"`
	ExpiresAt     int64    `json:"expiresAt"`
	Targets       []Target `json:"targets"`
}
type Request struct {
	OperationID string         `json:"operationId"`
	Kind        string         `json:"kind"`
	DocumentID  string         `json:"documentId,omitempty"`
	Desired     map[string]any `json:"desired"`
	Content     string         `json:"content"`
	Previous    map[string]any `json:"previous,omitempty"`
}
type Result struct {
	OperationID string `json:"operationId"`
	DocumentID  string `json:"documentId"`
	TaskID      string `json:"taskId"`
	State       string `json:"state"`
}
type Observation struct {
	Operation Result           `json:"operation"`
	Document  *types.Knowledge `json:"document"`
	Chunks    []*types.Chunk   `json:"chunks"`
	Proof     map[string]any   `json:"proof"`
}

type Store struct {
	db         *gorm.DB
	policy     Policy
	policyPath string
	policyHash string
	mu         sync.Mutex // Tiny first-round scope: serialize all four writes and observations.
}
type job struct {
	OperationID string
	InstanceID  string
	TenantID    uint64
	KBID        string
	SourceKey   string
	Payload     string
	PayloadHash string
	DocumentID  string
	ChunkID     string
	TaskID      string
	State       string
	Receipt     string
	Error       string
}

func (job) TableName() string { return "sync_canary_operations" }

type embedding struct {
	ID              int64
	CreatedAt       time.Time
	UpdatedAt       time.Time
	SourceID        string
	SourceType      int
	ChunkID         string
	KnowledgeID     string
	KnowledgeBaseID string
	TagID           string
	Content         string
	Dimension       int
	IsEnabled       bool
}

func (embedding) TableName() string { return "lite_embeddings" }

type posting struct {
	Term   string `json:"term"`
	Col    string `json:"col"`
	Offset int64  `json:"offset"`
}
type inventory struct {
	Document   *types.Knowledge `json:"document"`
	Chunks     []*types.Chunk   `json:"chunks"`
	Embeddings []embedding      `json:"embeddings"`
	Postings   []posting        `json:"postings"`
}

func encoded(v any) ([]byte, error) { return json.Marshal(v) }
func hashBytes(v []byte) string     { sum := sha256.Sum256(v); return hex.EncodeToString(sum[:]) }
func hashObject(v any) (string, error) {
	b, e := encoded(v)
	if e != nil {
		return "", e
	}
	return hashBytes(b), nil
}
func equalJSON(a, b any) bool {
	x, e := encoded(a)
	y, f := encoded(b)
	return e == nil && f == nil && bytes.Equal(x, y)
}
func text(m map[string]any, k string) string { s, _ := m[k].(string); return s }

func readPolicy(path string) (Policy, error) {
	var p Policy
	stat, e := os.Lstat(path)
	if e != nil {
		return p, e
	}
	if !stat.Mode().IsRegular() || stat.Size() > 65536 {
		return p, ErrDenied
	}
	f, e := os.Open(path)
	if e != nil {
		return p, e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, 65537))
	if e != nil || len(b) > 65536 {
		return p, ErrDenied
	}
	// Reject duplicate JSON keys as well as unknown top-level/struct fields.
	if e = uniqueJSON(b); e != nil {
		return p, e
	}
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if e = d.Decode(&p); e != nil {
		return p, ErrDenied
	}
	if e = d.Decode(new(any)); e != io.EOF {
		return p, ErrDenied
	}
	return p, p.validate(time.Now().Unix())
}
func uniqueJSON(b []byte) error {
	d := json.NewDecoder(bytes.NewReader(b))
	var value func() error
	value = func() error {
		t, e := d.Token()
		if e != nil {
			return e
		}
		delim, ok := t.(json.Delim)
		if !ok {
			return nil
		}
		switch delim {
		case '{':
			keys := map[string]bool{}
			for d.More() {
				k, e := d.Token()
				if e != nil {
					return e
				}
				s, ok := k.(string)
				if !ok || keys[s] {
					return ErrDenied
				}
				keys[s] = true
				if e = value(); e != nil {
					return e
				}
			}
		case '[':
			for d.More() {
				if e = value(); e != nil {
					return e
				}
			}
		default:
			return ErrDenied
		}
		_, e = d.Token()
		return e
	}
	if e := value(); e != nil {
		return ErrDenied
	}
	if _, e := d.Token(); e != io.EOF {
		return ErrDenied
	}
	return nil
}
func (p Policy) validate(now int64) error {
	if p.SchemaVersion != 1 || p.ExpiresAt <= now || len(p.Targets) != 2 || unicode.Version != "15.0.0" {
		return ErrDenied
	}
	if _, e := uuid.Parse(p.InstanceID); e != nil {
		return ErrDenied
	}
	channels := map[string]bool{}
	ids := map[string]bool{}
	for _, t := range p.Targets {
		expected := map[string]string{"local": "shidai3-dev-canary", "official": "shidai3-official-canary"}[t.Channel]
		if expected == "" || t.Name != expected || t.TenantID == 0 || channels[t.Channel] || ids[t.KBID] || len(t.Documents) != 2 {
			return ErrDenied
		}
		if _, e := uuid.Parse(t.KBID); e != nil {
			return ErrDenied
		}
		channels[t.Channel] = true
		ids[t.KBID] = true
		paths := map[string]bool{}
		for _, a := range t.Documents {
			m := a.Desired
			path := text(m, "path")
			if !hashPattern.MatchString(a.ContentSHA256) || (path != "AGENTS.md" && path != "AI_CONTEXT.md") || paths[path] || text(m, "channel") != t.Channel || fmt.Sprint(m["tenant_id"]) != fmt.Sprint(t.TenantID) || text(m, "kb_id") != t.KBID {
				return ErrDenied
			}
			for _, key := range []string{"owner", "repo_id", "title", "source_key"} {
				if text(m, key) == "" {
					return ErrDenied
				}
			}
			if !gitHashPattern.MatchString(text(m, "source_commit")) || !gitHashPattern.MatchString(text(m, "blob_sha")) || !gitHashPattern.MatchString(text(m, "parent_commit")) {
				return ErrDenied
			}
			if text(m, "source_key") != text(m, "repo_id")+":"+t.Channel+":"+path || !hashPattern.MatchString(text(m, "operation_id")) {
				return ErrDenied
			}
			paths[path] = true
		}
	}
	return nil
}

// New with an empty environment is entirely inert, including for a nil db.
func New(db *gorm.DB) (*Store, error) {
	s := &Store{db: db}
	path := os.Getenv("WEKNORA_SYNC_CANARY_POLICY")
	if path == "" {
		return s, nil
	}
	p, e := readPolicy(path)
	if e != nil {
		return nil, e
	}
	s.policy = p
	s.policyPath = path
	s.policyHash, e = hashObject(p)
	if e != nil {
		return nil, e
	}
	if db == nil || db.Dialector.Name() != "sqlite" {
		return nil, ErrDenied
	}
	if e = InitSchema(db, p); e != nil {
		return nil, e
	}
	return s, nil
}
func (s *Store) Enabled() bool { return s != nil && len(s.policy.Targets) == 2 && s.guard() == nil }
func (s *Store) Policy() Policy {
	var p Policy
	b, e := encoded(s.policy)
	if e == nil {
		_ = json.Unmarshal(b, &p)
	}
	return p
}
func (s *Store) guard() error {
	if s == nil || len(s.policy.Targets) != 2 {
		return ErrDisabled
	}
	if e := s.policy.validate(time.Now().Unix()); e != nil {
		return e
	}
	p, e := readPolicy(s.policyPath)
	if e != nil {
		return ErrDenied
	}
	h, e := hashObject(p)
	if e != nil || h != s.policyHash {
		return ErrDenied
	}
	return nil
}
func (s *Store) target(tenant uint64, kb string) (Target, error) {
	if e := s.guard(); e != nil {
		return Target{}, e
	}
	for _, t := range s.policy.Targets {
		if t.TenantID == tenant && t.KBID == kb {
			return t, nil
		}
	}
	return Target{}, ErrDenied
}

// InitSchema creates only canary-owned tables and the read-only FTS vocabulary.
// All adoption guards and DDL share a transaction; it never backfills application rows.
func InitSchema(db *gorm.DB, p Policy) error {
	if e := p.validate(time.Now().Unix()); e != nil {
		return e
	}
	if db == nil || db.Dialector.Name() != "sqlite" {
		return ErrDenied
	}
	policyHash, e := hashObject(p)
	if e != nil {
		return e
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if e := checkFTSSchema(tx, true); e != nil {
			return e
		}
		for _, q := range []string{
			`CREATE TABLE IF NOT EXISTS sync_canary_adoptions(kb_id TEXT PRIMARY KEY,tenant_id INTEGER NOT NULL,instance_id TEXT NOT NULL,policy_hash TEXT NOT NULL,created_at INTEGER NOT NULL)`,
			`CREATE TABLE IF NOT EXISTS sync_canary_operations(operation_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL,tenant_id INTEGER NOT NULL,kb_id TEXT NOT NULL,source_key TEXT NOT NULL,payload TEXT NOT NULL,payload_hash TEXT NOT NULL,document_id TEXT NOT NULL UNIQUE,chunk_id TEXT NOT NULL UNIQUE,task_id TEXT NOT NULL UNIQUE,state TEXT NOT NULL,receipt TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',UNIQUE(kb_id,source_key))`,
		} {
			if e := tx.Exec(q).Error; e != nil {
				return e
			}
			name := "sync_canary_operations"
			if strings.Contains(q, "sync_canary_adoptions(") {
				name = "sync_canary_adoptions"
			}
			var existing string
			if e := tx.Raw(`SELECT sql FROM sqlite_master WHERE type='table' AND name=?`, name).Scan(&existing).Error; e != nil {
				return e
			}
			if normalizeSQL(existing) != normalizeSQL(strings.ReplaceAll(q, "IF NOT EXISTS ", "")) {
				return ErrInventory
			}
		}
		for _, t := range p.Targets {
			if e := checkKB(tx, t); e != nil {
				return e
			}
			var a struct {
				TenantID   uint64
				InstanceID string
				PolicyHash string
			}
			r := tx.Raw(`SELECT tenant_id,instance_id,policy_hash FROM sync_canary_adoptions WHERE kb_id=?`, t.KBID).Scan(&a)
			if r.Error != nil {
				return r.Error
			}
			if r.RowsAffected == 0 {
				var n int64
				for _, table := range []string{"knowledges", "chunks", "lite_embeddings"} {
					column := "knowledge_base_id"
					if e := tx.Table(table).Where(column+"=?", t.KBID).Count(&n).Error; e != nil {
						return e
					}
					if n != 0 {
						return ErrDenied
					}
				}
				if e := tx.Exec(`INSERT INTO sync_canary_adoptions VALUES(?,?,?,?,?)`, t.KBID, t.TenantID, p.InstanceID, policyHash, time.Now().Unix()).Error; e != nil {
					return e
				}
			} else if a.TenantID != t.TenantID || a.InstanceID != p.InstanceID || a.PolicyHash != policyHash {
				return ErrDenied
			}
			// Reject historical, soft-deleted and externally-created rows on restart too.
			var n int64
			checks := []string{
				`SELECT count(*) FROM knowledges k WHERE k.knowledge_base_id=? AND NOT EXISTS(SELECT 1 FROM sync_canary_operations o WHERE o.document_id=k.id AND o.instance_id=? AND o.kb_id=k.knowledge_base_id AND o.tenant_id=k.tenant_id AND o.state='done')`,
				`SELECT count(*) FROM chunks c WHERE c.knowledge_base_id=? AND NOT EXISTS(SELECT 1 FROM sync_canary_operations o WHERE o.chunk_id=c.id AND o.document_id=c.knowledge_id AND o.instance_id=? AND o.kb_id=c.knowledge_base_id AND o.tenant_id=c.tenant_id AND o.state='done')`,
				`SELECT count(*) FROM lite_embeddings e WHERE e.knowledge_base_id=? AND NOT EXISTS(SELECT 1 FROM sync_canary_operations o WHERE o.chunk_id=e.chunk_id AND o.document_id=e.knowledge_id AND o.instance_id=? AND o.kb_id=e.knowledge_base_id AND o.state='done')`,
			}
			for _, q := range checks {
				if e := tx.Raw(q, t.KBID, p.InstanceID).Scan(&n).Error; e != nil {
					return e
				}
				if n != 0 {
					return ErrDenied
				}
			}
			if e := checkOwnedFTS(tx, t, p.InstanceID); e != nil {
				return e
			}
		}
		return nil
	})
}

func checkOwnedFTS(tx *gorm.DB, t Target, instance string) error {
	var actual, expected []int64
	match := `knowledge_base_id:"` + t.KBID + `"`
	if e := tx.Raw(`SELECT rowid FROM lite_embeddings_fts WHERE lite_embeddings_fts MATCH ? ORDER BY rowid`, match).Scan(&actual).Error; e != nil {
		return e
	}
	if e := tx.Raw(`SELECT e.id FROM lite_embeddings e JOIN sync_canary_operations o ON o.chunk_id=e.chunk_id AND o.document_id=e.knowledge_id WHERE e.knowledge_base_id=? AND o.kb_id=? AND o.instance_id=? AND o.state='done' ORDER BY e.id`, t.KBID, t.KBID, instance).Scan(&expected).Error; e != nil {
		return e
	}
	if len(actual) != len(expected) {
		return ErrInventory
	}
	for i := range actual {
		if actual[i] != expected[i] {
			return ErrInventory
		}
	}
	return nil
}

func (s *Store) checkOwnership(tx *gorm.DB, t Target) error {
	var adopted int64
	if e := tx.Raw(`SELECT count(*) FROM sync_canary_adoptions WHERE kb_id=? AND tenant_id=? AND instance_id=? AND policy_hash=?`, t.KBID, t.TenantID, s.policy.InstanceID, s.policyHash).Scan(&adopted).Error; e != nil {
		return e
	}
	if adopted != 1 {
		return ErrDenied
	}
	checks := []string{
		`SELECT count(*) FROM knowledges k WHERE k.knowledge_base_id=? AND NOT EXISTS(SELECT 1 FROM sync_canary_operations o WHERE o.document_id=k.id AND o.instance_id=? AND o.kb_id=k.knowledge_base_id AND o.tenant_id=k.tenant_id AND o.state='done')`,
		`SELECT count(*) FROM chunks c WHERE c.knowledge_base_id=? AND NOT EXISTS(SELECT 1 FROM sync_canary_operations o WHERE o.chunk_id=c.id AND o.document_id=c.knowledge_id AND o.instance_id=? AND o.kb_id=c.knowledge_base_id AND o.tenant_id=c.tenant_id AND o.state='done')`,
		`SELECT count(*) FROM lite_embeddings e WHERE e.knowledge_base_id=? AND NOT EXISTS(SELECT 1 FROM sync_canary_operations o WHERE o.chunk_id=e.chunk_id AND o.document_id=e.knowledge_id AND o.instance_id=? AND o.kb_id=e.knowledge_base_id AND o.state='done')`,
	}
	for _, q := range checks {
		var n int64
		if e := tx.Raw(q, t.KBID, s.policy.InstanceID).Scan(&n).Error; e != nil {
			return e
		}
		if n != 0 {
			return ErrDenied
		}
	}
	return checkOwnedFTS(tx, t, s.policy.InstanceID)
}
func checkKB(tx *gorm.DB, t Target) error {
	var kb types.KnowledgeBase
	if e := tx.Where("id=? AND tenant_id=?", t.KBID, t.TenantID).First(&kb).Error; e != nil {
		return e
	}
	if kb.Name != t.Name || kb.Type != types.KnowledgeBaseTypeDocument || kb.IsTemporary || kb.EmbeddingModelID != "" || kb.SummaryModelID != "" || kb.VLMConfig.Enabled || kb.VLMConfig.ModelID != "" || kb.ASRConfig.Enabled || kb.ASRConfig.ModelID != "" || kb.IndexingStrategy.VectorEnabled || !kb.IndexingStrategy.KeywordEnabled || kb.IndexingStrategy.GraphEnabled || kb.IndexingStrategy.WikiEnabled || kb.HasVectorStore() {
		return ErrDenied
	}
	if kb.ExtractConfig != nil && kb.ExtractConfig.Enabled || kb.QuestionGenerationConfig != nil && kb.QuestionGenerationConfig.Enabled || kb.AutoTagConfig != nil && (kb.AutoTagConfig.Enabled || kb.AutoTagConfig.ModelID != "") {
		return ErrDenied
	}
	// No profile config/model may introduce enrichment into this isolated write lane.
	if kb.ProfileConfig != nil {
		return ErrDenied
	}
	return nil
}
func normalizeSQL(s string) string { return strings.ToLower(strings.Join(strings.Fields(s), "")) }
func checkFTSSchema(tx *gorm.DB, initialize bool) error {
	var def string
	if e := tx.Raw(`SELECT sql FROM sqlite_master WHERE type='table' AND name='lite_embeddings_fts'`).Scan(&def).Error; e != nil {
		return e
	}
	expected := `CREATE VIRTUAL TABLE lite_embeddings_fts USING fts5(content,source_id,chunk_id,knowledge_id,knowledge_base_id,content='',contentless_delete=1,tokenize='unicode61')`
	if normalizeSQL(strings.ReplaceAll(def, "IF NOT EXISTS ", "")) != normalizeSQL(expected) {
		return ErrInventory
	}
	var n int64
	if e := tx.Raw(`SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('knowledges','chunks','lite_embeddings','lite_embeddings_fts')`).Scan(&n).Error; e != nil {
		return e
	}
	if n != 0 {
		return ErrDenied
	}
	var vocab string
	if e := tx.Raw(`SELECT sql FROM sqlite_master WHERE name='lite_embeddings_fts_vocab'`).Scan(&vocab).Error; e != nil {
		return e
	}
	vsql := `CREATE VIRTUAL TABLE lite_embeddings_fts_vocab USING fts5vocab(lite_embeddings_fts, 'instance')`
	if vocab == "" {
		if !initialize {
			return ErrInventory
		}
		return tx.Exec(vsql).Error
	}
	if normalizeSQL(strings.ReplaceAll(vocab, "IF NOT EXISTS ", "")) != normalizeSQL(vsql) {
		return ErrInventory
	}
	return nil
}

func (s *Store) validateRequest(t Target, r Request) error {
	if r.Kind != "add" || r.DocumentID != "" || len(r.Previous) != 0 {
		return ErrUnsupported
	}
	if !operationPattern.MatchString(r.OperationID) || !hashPattern.MatchString(r.OperationID) || text(r.Desired, "operation_id") != r.OperationID || !utf8.ValidString(r.Content) || len(r.Content) == 0 || len(r.Content) > 262144 {
		return ErrDenied
	}
	for _, a := range t.Documents {
		if equalJSON(a.Desired, r.Desired) && a.ContentSHA256 == hashBytes([]byte(r.Content)) {
			return nil
		}
	}
	return ErrDenied
}
func (s *Store) Accept(ctx context.Context, tenant uint64, kb string, r Request) (Result, error) {
	t, e := s.target(tenant, kb)
	if e != nil {
		return Result{}, e
	}
	if e = s.validateRequest(t, r); e != nil {
		return Result{}, e
	}
	b, e := encoded(r)
	if e != nil {
		return Result{}, e
	}
	payloadHash := hashBytes(b)
	s.mu.Lock()
	var j job
	e = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if e := checkKB(tx, t); e != nil {
			return e
		}
		if e := s.checkOwnership(tx, t); e != nil {
			return e
		}
		if e := s.guard(); e != nil {
			return e
		}
		q := tx.Where("operation_id=?", r.OperationID).First(&j)
		if q.Error == nil {
			if j.InstanceID != s.policy.InstanceID || j.TenantID != tenant || j.KBID != kb || j.PayloadHash != payloadHash {
				return ErrConflict
			}
			return nil
		}
		if !errors.Is(q.Error, gorm.ErrRecordNotFound) {
			return q.Error
		}
		j = job{OperationID: r.OperationID, InstanceID: s.policy.InstanceID, TenantID: tenant, KBID: kb, SourceKey: text(r.Desired, "source_key"), Payload: string(b), PayloadHash: payloadHash, DocumentID: uuid.NewString(), ChunkID: uuid.NewString(), TaskID: uuid.NewString(), State: "queued"}
		return tx.Create(&j).Error
	})
	s.mu.Unlock()
	if e != nil {
		return Result{}, e
	}
	if j.State == "queued" || j.State == "running" {
		go func() { _ = s.Process(context.Background(), j.OperationID) }()
	}
	return Result{j.OperationID, j.DocumentID, j.TaskID, j.State}, nil
}
func (s *Store) loadJob(tx *gorm.DB, id string) (job, error) {
	var j job
	e := tx.Where("operation_id=? AND instance_id=?", id, s.policy.InstanceID).First(&j).Error
	return j, e
}
func (s *Store) Process(ctx context.Context, id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e := s.guard(); e != nil {
		return e
	}
	j, e := s.loadJob(s.db.WithContext(ctx), id)
	if e != nil {
		return e
	}
	if j.State == "done" || j.State == "failed" {
		return nil
	}
	t, e := s.target(j.TenantID, j.KBID)
	if e != nil {
		return e
	}
	var r Request
	if json.Unmarshal([]byte(j.Payload), &r) != nil || hashBytes([]byte(j.Payload)) != j.PayloadHash || r.OperationID != j.OperationID {
		return ErrConflict
	}
	if e = s.validateRequest(t, r); e != nil {
		return e
	}
	q := s.db.WithContext(ctx).Model(&job{}).Where("operation_id=? AND state IN ('queued','running')", id).Update("state", "running")
	if q.Error != nil {
		return q.Error
	}
	if q.RowsAffected != 1 {
		return ErrConflict
	}
	e = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if e := s.guard(); e != nil {
			return e
		}
		if e := checkKB(tx, t); e != nil {
			return e
		}
		if e := checkFTSSchema(tx, false); e != nil {
			return e
		}
		if e := s.checkOwnership(tx, t); e != nil {
			return e
		}
		var current job
		if e := tx.Where("operation_id=? AND state='running'", id).First(&current).Error; e != nil {
			return e
		}
		now := time.Now().UTC()
		metadata, e := types.NewManualKnowledgeMetadata(r.Content, types.ManualKnowledgeStatusPublish, 1).ToJSON()
		if e != nil {
			return e
		}
		desired, e := encoded(r.Desired)
		if e != nil {
			return e
		}
		k := types.Knowledge{ID: j.DocumentID, TenantID: j.TenantID, KnowledgeBaseID: j.KBID, Type: types.KnowledgeTypeManual, Title: text(r.Desired, "title"), Source: "manual", Channel: t.Channel, ParseStatus: types.ParseStatusCompleted, SummaryStatus: types.SummaryStatusNone, EnableStatus: "enabled", FileName: text(r.Desired, "path"), FileType: "md", FileSize: int64(len(r.Content)), FileHash: hashBytes([]byte(r.Content)), StorageSize: int64(len(r.Content)), Metadata: metadata, CustomMetadata: types.JSON(desired), ProcessedAt: &now, CreatedAt: now, UpdatedAt: now}
		if e := tx.Create(&k).Error; e != nil {
			return e
		}
		c := &types.Chunk{ID: j.ChunkID, TenantID: j.TenantID, KnowledgeID: j.DocumentID, KnowledgeBaseID: j.KBID, Content: r.Content, SourceContent: r.Content, ContentHash: hashBytes([]byte(r.Content)), IndexStatus: "ready", ChunkIndex: 0, IsEnabled: true, Status: int(types.ChunkStatusIndexed), EndAt: utf8.RuneCountInString(r.Content), ChunkType: types.ChunkTypeText, CreatedAt: now, UpdatedAt: now}
		if e := types.AssignChunkSeqIDs(tx, []*types.Chunk{c}); e != nil {
			return e
		}
		if e := tx.Create(c).Error; e != nil {
			return e
		}
		input := strings.TrimSpace(k.Title) + "\n" + strings.TrimSpace(r.Content)
		emb := embedding{CreatedAt: now, UpdatedAt: now, SourceID: c.ID, SourceType: int(types.ChunkSourceType), ChunkID: c.ID, KnowledgeID: k.ID, KnowledgeBaseID: j.KBID, Content: input, IsEnabled: true}
		if e := tx.Create(&emb).Error; e != nil {
			return e
		}
		if e := tx.Exec(`INSERT INTO lite_embeddings_fts(rowid,content,source_id,chunk_id,knowledge_id,knowledge_base_id) VALUES(?,?,?,?,?,?)`, emb.ID, tokenizeCJKBigram(input), emb.SourceID, emb.ChunkID, emb.KnowledgeID, emb.KnowledgeBaseID).Error; e != nil {
			return e
		}
		inv, e := s.inventory(tx, j, r)
		if e != nil {
			return e
		}
		invHash, e := hashObject(inv)
		if e != nil {
			return e
		}
		postHash, e := hashObject(inv.Postings)
		if e != nil {
			return e
		}
		receipt, e := encoded(map[string]any{"inventoryHash": invHash, "postingsHash": postHash, "embeddingRowID": emb.ID, "documentVersion": 1, "contentSHA256": hashBytes([]byte(r.Content))})
		if e != nil {
			return e
		}
		q := tx.Model(&job{}).Where("operation_id=? AND state='running'", id).Updates(map[string]any{"state": "done", "receipt": string(receipt), "error": ""})
		if q.Error != nil {
			return q.Error
		}
		if q.RowsAffected != 1 {
			return ErrConflict
		}
		return nil
	})
	if e != nil {
		// Rollback has removed every application row. Store only a bounded failure code.
		q := s.db.WithContext(context.Background()).Model(&job{}).Where("operation_id=? AND state='running'", id).Updates(map[string]any{"state": "failed", "error": "atomic_keyword_write_failed"})
		if q.Error != nil {
			return errors.Join(e, q.Error)
		}
	}
	return e
}

// inventory reads complete current rows and actual FTS postings in one DB snapshot.
func (s *Store) inventory(tx *gorm.DB, j job, r Request) (inventory, error) {
	var inv inventory
	var docs []*types.Knowledge
	if e := tx.Unscoped().Where("id=? OR (knowledge_base_id=? AND json_extract(custom_metadata,'$.source_key')=?)", j.DocumentID, j.KBID, j.SourceKey).Find(&docs).Error; e != nil {
		return inv, e
	}
	if len(docs) != 1 {
		return inv, ErrInventory
	}
	k := docs[0]
	inv.Document = k
	var metadata types.ManualKnowledgeMetadata
	var custom map[string]any
	if json.Unmarshal(k.Metadata, &metadata) != nil || json.Unmarshal(k.CustomMetadata, &custom) != nil || !equalJSON(custom, r.Desired) || k.ID != j.DocumentID || k.TenantID != j.TenantID || k.KnowledgeBaseID != j.KBID || k.DeletedAt.Valid || k.Type != "manual" || k.Title != text(r.Desired, "title") || k.Channel != text(r.Desired, "channel") || k.ParseStatus != "completed" || k.EnableStatus != "enabled" || k.SummaryStatus != "none" || k.PendingSubtasksCount != 0 || k.EmbeddingModelID != "" || metadata.Content != r.Content || metadata.Format != "markdown" || metadata.Status != "publish" || metadata.Version != 1 {
		return inv, ErrInventory
	}
	if e := tx.Unscoped().Where("knowledge_id=? OR id=?", j.DocumentID, j.ChunkID).Order("id").Find(&inv.Chunks).Error; e != nil {
		return inv, e
	}
	if len(inv.Chunks) != 1 {
		return inv, ErrInventory
	}
	c := inv.Chunks[0]
	if c.ID != j.ChunkID || c.KnowledgeID != j.DocumentID || c.TenantID != j.TenantID || c.KnowledgeBaseID != j.KBID || c.DeletedAt.Valid || c.Content != r.Content || c.SourceContent != r.Content || c.ContentHash != hashBytes([]byte(r.Content)) || c.ContentRevision != 0 || c.IndexStatus != "ready" || !c.IsEnabled || c.Status != int(types.ChunkStatusIndexed) || c.ChunkType != "text" || c.ContextHeader != "" || c.ParentChunkID != "" || c.ChunkIndex != 0 {
		return inv, ErrInventory
	}
	if e := tx.Where("knowledge_id=? OR chunk_id=? OR source_id=?", j.DocumentID, j.ChunkID, j.ChunkID).Order("id").Find(&inv.Embeddings).Error; e != nil {
		return inv, e
	}
	if len(inv.Embeddings) != 1 {
		return inv, ErrInventory
	}
	emb := inv.Embeddings[0]
	input := strings.TrimSpace(k.Title) + "\n" + strings.TrimSpace(r.Content)
	if emb.ID <= 0 || emb.SourceID != c.ID || emb.ChunkID != c.ID || emb.SourceType != 0 || emb.KnowledgeID != k.ID || emb.KnowledgeBaseID != j.KBID || emb.TagID != "" || emb.Content != input || emb.Dimension != 0 || !emb.IsEnabled {
		return inv, ErrInventory
	}
	var count int64
	if e := tx.Raw(`SELECT count(*) FROM lite_embeddings_fts WHERE rowid=?`, emb.ID).Scan(&count).Error; e != nil {
		return inv, e
	}
	if count != 1 {
		return inv, ErrInventory
	}
	var rowids []int64
	match := `knowledge_id:"` + j.DocumentID + `" OR chunk_id:"` + j.ChunkID + `" OR source_id:"` + j.ChunkID + `"`
	if e := tx.Raw(`SELECT rowid FROM lite_embeddings_fts WHERE lite_embeddings_fts MATCH ? ORDER BY rowid`, match).Scan(&rowids).Error; e != nil {
		return inv, e
	}
	if len(rowids) != 1 || rowids[0] != emb.ID {
		return inv, ErrInventory
	}
	if e := tx.Raw(`SELECT term,col,offset FROM lite_embeddings_fts_vocab WHERE doc=? ORDER BY col,offset,term`, emb.ID).Scan(&inv.Postings).Error; e != nil {
		return inv, e
	}
	columns := map[string]bool{}
	for _, p := range inv.Postings {
		columns[p.Col] = true
	}
	for _, col := range []string{"content", "source_id", "chunk_id", "knowledge_id", "knowledge_base_id"} {
		if !columns[col] {
			return inv, ErrInventory
		}
	}
	return inv, nil
}
func (s *Store) Observe(ctx context.Context, tenant uint64, kb, id string) (Observation, error) {
	t, e := s.target(tenant, kb)
	if e != nil {
		return Observation{}, e
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := Observation{Chunks: []*types.Chunk{}, Proof: map[string]any{"schemaVersion": 1, "providerId": ProviderID, "observedAt": time.Now().Unix(), "state": "unknown"}}
	e = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if e := checkKB(tx, t); e != nil {
			return e
		}
		if e := checkFTSSchema(tx, false); e != nil {
			return e
		}
		if e := s.checkOwnership(tx, t); e != nil {
			return e
		}
		j, e := s.loadJob(tx, id)
		if e != nil {
			return e
		}
		if j.TenantID != tenant || j.KBID != kb {
			return ErrDenied
		}
		out.Operation = Result{j.OperationID, j.DocumentID, j.TaskID, j.State}
		var r Request
		if json.Unmarshal([]byte(j.Payload), &r) != nil || hashBytes([]byte(j.Payload)) != j.PayloadHash || r.OperationID != id {
			return ErrConflict
		}
		if e = s.validateRequest(t, r); e != nil {
			return e
		}
		binding := map[string]any{}
		for k, v := range r.Desired {
			binding[k] = v
		}
		binding["document_id"] = j.DocumentID
		binding["operation_id"] = j.OperationID
		out.Proof["binding"] = binding
		out.Proof["taskId"] = j.TaskID
		if j.State == "queued" || j.State == "running" {
			out.Proof["state"] = "pending"
			out.Proof["taskState"] = j.State
			return nil
		}
		if j.State == "failed" {
			out.Proof["state"] = "failed"
			out.Proof["taskState"] = "failed"
			return nil
		}
		if j.State != "done" {
			return nil
		}
		inv, e := s.inventory(tx, j, r)
		if e != nil {
			if errors.Is(e, ErrInventory) {
				return nil
			}
			return e
		}
		var receipt struct {
			InventoryHash   string
			PostingsHash    string
			EmbeddingRowID  int64
			DocumentVersion int
			ContentSHA256   string
		}
		if json.Unmarshal([]byte(j.Receipt), &receipt) != nil {
			return nil
		}
		h, e := hashObject(inv)
		if e != nil {
			return e
		}
		ph, e := hashObject(inv.Postings)
		if e != nil {
			return e
		}
		if h != receipt.InventoryHash || ph != receipt.PostingsHash || receipt.EmbeddingRowID != inv.Embeddings[0].ID || receipt.DocumentVersion != 1 || receipt.ContentSHA256 != hashBytes([]byte(r.Content)) {
			return nil
		}
		out.Document = inv.Document
		out.Chunks = inv.Chunks
		out.Proof["state"] = "ready"
		out.Proof["taskState"] = "done"
		out.Proof["documentVersion"] = 1
		out.Proof["contentSHA256"] = receipt.ContentSHA256
		out.Proof["documentChunkCount"] = 1
		out.Proof["backendInventoryHash"] = h
		out.Proof["indexes"] = map[string]any{"keyword": map[string]any{"state": "done", "documentVersion": 1, "totalChunks": 1, "chunks": []map[string]any{{"id": j.ChunkID, "contentSHA256": receipt.ContentSHA256}}, "postingsSHA256": ph}}
		return nil
	})
	return out, e
}
func (s *Store) Resume() error {
	if e := s.guard(); e != nil {
		return e
	}
	s.mu.Lock()
	var jobs []job
	e := s.db.Where("instance_id=? AND state IN ('queued','running')", s.policy.InstanceID).Order("operation_id").Find(&jobs).Error
	s.mu.Unlock()
	if e != nil {
		return e
	}
	for _, j := range jobs {
		id := j.OperationID
		go func() { _ = s.Process(context.Background(), id) }()
	}
	return nil
}

// Copied from the pinned SQLite retriever: unicode tables must remain 15.0.0.
func tokenizeCJKBigram(input string) string {
	var parts []string
	var han []rune
	var other strings.Builder
	flushHan := func() {
		if len(han) == 1 {
			parts = append(parts, string(han[0]))
		} else {
			for i := 0; i < len(han)-1; i++ {
				parts = append(parts, string(han[i])+string(han[i+1]))
			}
		}
		han = han[:0]
	}
	flushOther := func() {
		if other.Len() > 0 {
			parts = append(parts, other.String())
			other.Reset()
		}
	}
	for _, r := range input {
		if unicode.Is(unicode.Han, r) {
			flushOther()
			han = append(han, r)
		} else if unicode.IsSpace(r) || unicode.IsPunct(r) || unicode.IsSymbol(r) {
			flushHan()
			flushOther()
		} else {
			flushHan()
			other.WriteRune(r)
		}
	}
	flushHan()
	flushOther()
	return strings.Join(parts, " ")
}

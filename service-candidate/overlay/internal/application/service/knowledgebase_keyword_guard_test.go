package service

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	sqliterepo "github.com/Tencent/WeKnora/internal/application/repository/retriever/sqlite"
	"github.com/Tencent/WeKnora/internal/application/service/retriever"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/stretchr/testify/require"
	gormsqlite "gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

type localKeywordKBRepo struct {
	interfaces.KnowledgeBaseRepository
	rows []*types.KnowledgeBase
}

func (r *localKeywordKBRepo) GetKnowledgeBaseByIDs(context.Context, []string) ([]*types.KnowledgeBase, error) {
	return r.rows, nil
}

type localKeywordKnowledgeRepo struct{ interfaces.KnowledgeRepository }

func (*localKeywordKnowledgeRepo) GetKnowledgeBatch(context.Context, uint64, []string) ([]*types.Knowledge, error) {
	return []*types.Knowledge{{ID: "synthetic-doc", TenantID: 1, KnowledgeBaseID: "synthetic-kb", Title: "Synthetic document"}}, nil
}

type localKeywordChunkRepo struct{ interfaces.ChunkRepository }

func (*localKeywordChunkRepo) ListChunksByID(context.Context, uint64, []string) ([]*types.Chunk, error) {
	return []*types.Chunk{{ID: "synthetic-chunk", TenantID: 1, KnowledgeID: "synthetic-doc", KnowledgeBaseID: "synthetic-kb", Content: "Synthetic Cocos keyword", ChunkType: types.ChunkTypeText, IsEnabled: true, ImageInfo: "[]"}}, nil
}

type localKeywordModelDenied struct{ interfaces.ModelService }

func (*localKeywordModelDenied) GetModelByID(context.Context, string) (*types.Model, error) {
	panic("keyword path resolved a model")
}

type localKeywordRegistry struct {
	interfaces.RetrieveEngineRegistry
	gets int
}

func (r *localKeywordRegistry) GetRetrieveEngineService(e types.RetrieverEngineType) (interfaces.RetrieveEngineService, error) {
	if e != types.SQLiteRetrieverEngineType {
		panic("keyword path reached remote engine")
	}
	r.gets++
	return r.RetrieveEngineRegistry.GetRetrieveEngineService(e)
}
func (*localKeywordRegistry) GetOrLoadByStoreID(context.Context, uint64, string) (interfaces.RetrieveEngineService, error) {
	panic("keyword path dialed a bound store")
}

type localKeywordNetworkDenied struct{ calls *atomic.Int32 }

func (d localKeywordNetworkDenied) RoundTrip(*http.Request) (*http.Response, error) {
	d.calls.Add(1)
	return nil, errors.New("outbound HTTP denied by fixture")
}

func TestLocalKeywordGuardRealHybridSQLite(t *testing.T) {
	var outbound atomic.Int32
	old := http.DefaultTransport
	http.DefaultTransport = localKeywordNetworkDenied{&outbound}
	defer func() { http.DefaultTransport = old }()
	cases := []string{"ready", "missing-fts", "empty-index", "incomplete-index", "remote-binding", "multi-kb", "remote-tenant-engine", "vector-only-engine", "mixed-engine", "foreign-tenant", "keyword-disabled", "faq", "unsafe-parameters", "ignored-orphan", "ignored-deleted-chunk", "ignored-deleted-document", "ignored-disabled", "ignored-staging", "ignored-parent", "ignored-unknown"}
	for _, name := range cases {
		t.Run(name, func(t *testing.T) {
			db, err := gorm.Open(gormsqlite.Open(fmt.Sprintf("file:guard-%s?mode=memory&cache=shared", name)), &gorm.Config{})
			require.NoError(t, err)
			sqlDB, err := db.DB()
			require.NoError(t, err)
			t.Cleanup(func() { require.NoError(t, sqlDB.Close()) })
			repo := sqliterepo.NewSQLiteRetrieveEngineRepository(db)
			require.NoError(t, db.Exec("CREATE TABLE chunks(id TEXT,knowledge_id TEXT,knowledge_base_id TEXT,is_enabled INTEGER,index_status TEXT,deleted_at TEXT,chunk_type TEXT)").Error)
			require.NoError(t, db.Exec("CREATE TABLE knowledges(id TEXT, knowledge_base_id TEXT, deleted_at TEXT)").Error)
			require.NoError(t, db.Exec("INSERT INTO chunks VALUES('synthetic-chunk','synthetic-doc','synthetic-kb',1,'ready',NULL,'text')").Error)
			require.NoError(t, db.Exec("INSERT INTO knowledges VALUES('synthetic-doc','synthetic-kb',NULL)").Error)

			if name != "empty-index" {
				require.NoError(t, repo.Save(context.Background(), &types.IndexInfo{SourceID: "synthetic-source", SourceType: types.ChunkSourceType, ChunkID: "synthetic-chunk", KnowledgeID: "synthetic-doc", KnowledgeBaseID: "synthetic-kb", Content: "Synthetic Cocos keyword", IsEnabled: true}, nil))
			}

			if strings.HasPrefix(name, "ignored-") {
				require.NoError(t, repo.Save(context.Background(), &types.IndexInfo{SourceID: "stale-source", SourceType: types.ChunkSourceType, ChunkID: "stale-chunk", KnowledgeID: "stale-doc", KnowledgeBaseID: "synthetic-kb", Content: "stale ignored metadata", IsEnabled: true}, nil))
				require.NoError(t, db.Exec("DELETE FROM lite_embeddings_fts WHERE rowid=(SELECT id FROM lite_embeddings WHERE source_id='stale-source')").Error)
				if name != "ignored-orphan" {
					require.NoError(t, db.Exec("INSERT INTO chunks VALUES('stale-chunk','stale-doc','synthetic-kb',1,'ready',NULL,'text')").Error)
					require.NoError(t, db.Exec("INSERT INTO knowledges VALUES('stale-doc','synthetic-kb',NULL)").Error)
				}
				switch name {
				case "ignored-deleted-chunk":
					require.NoError(t, db.Exec("UPDATE chunks SET deleted_at='2026-10-01' WHERE id='stale-chunk'").Error)
				case "ignored-deleted-document":
					require.NoError(t, db.Exec("UPDATE knowledges SET deleted_at='2026-10-01' WHERE id='stale-doc'").Error)
				case "ignored-disabled":
					require.NoError(t, db.Exec("UPDATE chunks SET is_enabled=0 WHERE id='stale-chunk'").Error)
				case "ignored-parent":
					require.NoError(t, db.Exec("UPDATE chunks SET chunk_type='parent_text' WHERE id='stale-chunk'").Error)
				case "ignored-unknown":
					require.NoError(t, db.Exec("UPDATE chunks SET chunk_type='unknown' WHERE id='stale-chunk'").Error)
				case "ignored-staging":
					require.NoError(t, db.Exec("UPDATE chunks SET index_status='processing' WHERE id='stale-chunk'").Error)
				}
			}
			if name == "missing-fts" {
				require.NoError(t, db.Exec("DROP TABLE lite_embeddings_fts").Error)
			}
			if name == "incomplete-index" {
				require.NoError(t, db.Exec("DELETE FROM lite_embeddings_fts").Error)
			}
			registry := &localKeywordRegistry{RetrieveEngineRegistry: retriever.NewRetrieveEngineRegistry(nil, nil)}
			require.NoError(t, registry.Register(retriever.NewKVHybridRetrieveEngine(repo, types.SQLiteRetrieverEngineType)))
			kb := &types.KnowledgeBase{ID: "synthetic-kb", TenantID: 1, Type: types.KnowledgeBaseTypeDocument, EmbeddingModelID: "must-not-be-used"}
			kb.IndexingStrategy.KeywordEnabled = true
			kb.IndexingStrategy.VectorEnabled = true
			engines := []types.RetrieverEngineParams{{RetrieverType: types.KeywordsRetrieverType, RetrieverEngineType: types.SQLiteRetrieverEngineType}, {RetrieverType: types.VectorRetrieverType, RetrieverEngineType: types.SQLiteRetrieverEngineType}}
			ids := []string{kb.ID}
			rows := []*types.KnowledgeBase{kb}
			params := types.SearchParams{LocalKeywordOnly: true, QueryText: "Cocos", DisableVectorMatch: true, MatchCount: 3, SkipContextEnrichment: true}
			switch name {
			case "remote-binding":
				remote := "synthetic-remote-store"
				kb.VectorStoreID = &remote
			case "multi-kb":
				second := *kb
				second.ID = "synthetic-kb-2"
				ids = append(ids, second.ID)
				rows = append(rows, &second)
			case "remote-tenant-engine":
				engines[0].RetrieverEngineType = types.QdrantRetrieverEngineType
			case "vector-only-engine":
				engines = engines[1:]
			case "mixed-engine":
				engines = append(engines, types.RetrieverEngineParams{RetrieverType: types.VectorRetrieverType, RetrieverEngineType: types.QdrantRetrieverEngineType})
			case "foreign-tenant":
				kb.TenantID = 2
			case "keyword-disabled":
				kb.IndexingStrategy.KeywordEnabled = false
			case "faq":
				kb.Type = types.KnowledgeBaseTypeFAQ
			case "unsafe-parameters":
				params.DisableVectorMatch = false
			}
			params.KnowledgeBaseIDs = ids
			tenant := &types.Tenant{ID: 1, RetrieverEngines: types.RetrieverEngines{Engines: engines}}
			ctx := context.WithValue(context.Background(), types.TenantIDContextKey, uint64(1))
			ctx = context.WithValue(ctx, types.TenantInfoContextKey, tenant)
			svc := &knowledgeBaseService{repo: &localKeywordKBRepo{rows: rows}, kgRepo: &localKeywordKnowledgeRepo{}, chunkRepo: &localKeywordChunkRepo{}, retrieveEngine: registry, modelService: &localKeywordModelDenied{}}
			result, err := svc.HybridSearch(ctx, kb.ID, params)
			if name == "ready" || strings.HasPrefix(name, "ignored-") {
				require.NoError(t, err)
				require.Len(t, result, 1)
				require.Equal(t, types.MatchTypeKeywords, result[0].MatchType)
			} else {
				require.Error(t, err)
				require.Empty(t, result)
			}
			if name == "remote-binding" || name == "multi-kb" || name == "remote-tenant-engine" || name == "vector-only-engine" || name == "mixed-engine" || name == "keyword-disabled" || name == "faq" || name == "unsafe-parameters" {
				require.Zero(t, registry.gets, "unsafe configuration must stop before engine resolution")
			}
		})
	}
	require.Zero(t, outbound.Load(), "no outbound HTTP from real HybridSearch/SQLite path")
}

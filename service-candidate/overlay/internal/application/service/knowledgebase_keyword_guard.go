package service

import (
	"context"
	"fmt"
	"slices"

	"github.com/Tencent/WeKnora/internal/types"
)

// validateLocalKeywordSearch runs before model resolution or store factories.
// The dedicated MCP grep path intentionally supports one local document KB.
func (s *knowledgeBaseService) validateLocalKeywordSearch(ctx context.Context, id string, ids []string, kbs []*types.KnowledgeBase, p types.SearchParams) error {
	fail := func(reason string) error {
		return fmt.Errorf("local keyword retrieval unavailable: %s; semantic fallback is disabled", reason)
	}
	if len(ids) != 1 || len(kbs) != 1 || ids[0] != id || !p.DisableVectorMatch || p.DisableKeywordsMatch || len(p.QueryEmbedding) != 0 {
		return fail("one knowledge base and keyword-only parameters are required")
	}
	kb := kbs[0]
	tenant, ok := types.TenantInfoFromContext(ctx)
	if !ok || tenant == nil || kb == nil || kb.ID != id || kb.TenantID != tenant.ID || kb.Type != types.KnowledgeBaseTypeDocument || !kb.IsKeywordEnabled() || kb.HasVectorStore() {
		return fail("document keyword indexing in the current tenant without a bound store is required")
	}
	keyword := false
	for _, e := range tenant.GetEffectiveEngines() {
		if e.RetrieverEngineType != types.SQLiteRetrieverEngineType || (e.RetrieverType != types.KeywordsRetrieverType && e.RetrieverType != types.VectorRetrieverType) {
			return fail("only the local SQLite engine is permitted")
		}
		keyword = keyword || e.RetrieverType == types.KeywordsRetrieverType
	}
	if !keyword || s.retrieveEngine == nil {
		return fail("SQLite keyword capability is missing")
	}
	engine, err := s.retrieveEngine.GetRetrieveEngineService(types.SQLiteRetrieverEngineType)
	if err != nil || engine == nil || engine.EngineType() != types.SQLiteRetrieverEngineType || !slices.Contains(engine.Support(), types.KeywordsRetrieverType) {
		return fail("the registered SQLite keyword engine is unavailable")
	}
	validator, ok := engine.(interface {
		ValidateLocalKeywordIndex(context.Context, string) error
	})
	if !ok {
		return fail("the registered engine cannot validate its local index")
	}
	if err := validator.ValidateLocalKeywordIndex(ctx, id); err != nil {
		return fail("the physical local keyword index is missing or incomplete")
	}
	return nil
}

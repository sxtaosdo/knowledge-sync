package tools

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/Tencent/WeKnora/internal/models/rerank"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

type keywordGuardKBFixture struct {
	interfaces.KnowledgeBaseService
	kb           *types.KnowledgeBase
	lookupError  bool
	missingIndex bool
	hybridCalls  int
}

func (f *keywordGuardKBFixture) GetKnowledgeBasesByIDsOnly(context.Context, []string) ([]*types.KnowledgeBase, error) {
	if f.lookupError {
		return nil, errors.New("synthetic lookup failure")
	}
	if f.kb == nil {
		return nil, nil
	}
	return []*types.KnowledgeBase{f.kb}, nil
}
func (f *keywordGuardKBFixture) ResolveEmbeddingModelKeys(context.Context, []*types.KnowledgeBase) map[string]string {
	panic("strict grep reached model resolution")
}
func (f *keywordGuardKBFixture) GetQueryEmbedding(context.Context, string, string) ([]float32, error) {
	panic("strict grep reached embedding")
}
func (f *keywordGuardKBFixture) HybridSearch(_ context.Context, _ string, p types.SearchParams) ([]*types.SearchResult, error) {
	f.hybridCalls++
	if !p.DisableVectorMatch || p.DisableKeywordsMatch || len(p.QueryEmbedding) != 0 {
		panic("strict grep enabled vectors")
	}
	if f.missingIndex {
		return nil, errors.New("synthetic missing FTS index")
	}
	return []*types.SearchResult{{ID: "fixture-chunk", KnowledgeID: "fixture-doc", KnowledgeBaseID: "fixture-kb", Content: "synthetic Cocos keyword", Score: 1}}, nil
}

type keywordGuardDenyNetwork struct{ calls *atomic.Int32 }

func (d keywordGuardDenyNetwork) RoundTrip(*http.Request) (*http.Response, error) {
	d.calls.Add(1)
	return nil, errors.New("synthetic network denied")
}

func TestKeywordGuardFailClosed(t *testing.T) {
	var network atomic.Int32
	old := http.DefaultTransport
	http.DefaultTransport = keywordGuardDenyNetwork{&network}
	defer func() { http.DefaultTransport = old }()
	cases := []struct {
		name        string
		kind        string
		keyword     bool
		lookup      bool
		missing     bool
		wantCalls   int
		wantSuccess bool
	}{
		{"document-keyword", types.KnowledgeBaseTypeDocument, true, false, false, 1, true},
		{"vector-only", types.KnowledgeBaseTypeDocument, false, false, false, 0, false},
		{"faq", types.KnowledgeBaseTypeFAQ, true, false, false, 0, false},
		{"non-document", "wiki", true, false, false, 0, false},
		{"missing-lookup", types.KnowledgeBaseTypeDocument, true, true, false, 0, false},
		{"missing-physical-index", types.KnowledgeBaseTypeDocument, true, false, true, 1, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			kb := &types.KnowledgeBase{ID: "fixture-kb", Type: tc.kind, EmbeddingModelID: "must-not-be-used"}
			kb.IndexingStrategy.KeywordEnabled = tc.keyword
			kb.IndexingStrategy.VectorEnabled = true
			f := &keywordGuardKBFixture{kb: kb, lookupError: tc.lookup, missingIndex: tc.missing}
			tool := NewSearchKnowledgeTool(f, nil, nil, types.SearchTargets{&types.SearchTarget{Type: types.SearchTargetTypeKnowledgeBase, KnowledgeBaseID: kb.ID, TenantID: 1}}, nil, nil).WithStrictKeywordOnly()
			args, _ := json.Marshal(map[string]any{"query": "Cocos", "mode": "keyword", "knowledge_base_ids": []string{kb.ID}, "limit": 3})
			result, err := tool.Execute(context.Background(), args)
			if result == nil || result.Success != tc.wantSuccess {
				t.Fatalf("unexpected guarded result: %v", err)
			}
			if !tc.wantSuccess && err == nil {
				t.Fatal("unsafe fixture must return diagnostic error")
			}
			if f.hybridCalls != tc.wantCalls {
				t.Fatalf("retrieval calls=%d expected=%d", f.hybridCalls, tc.wantCalls)
			}
		})
	}
	if network.Load() != 0 {
		t.Fatalf("outbound HTTP calls=%d", network.Load())
	}
}

func TestKeywordGuardRejectsSemanticMode(t *testing.T) {
	kb := &types.KnowledgeBase{ID: "fixture-kb", Type: types.KnowledgeBaseTypeDocument}
	kb.IndexingStrategy.KeywordEnabled = true
	f := &keywordGuardKBFixture{kb: kb}
	tool := NewSearchKnowledgeTool(f, nil, nil, types.SearchTargets{&types.SearchTarget{Type: types.SearchTargetTypeKnowledgeBase, KnowledgeBaseID: kb.ID, TenantID: 1}}, nil, nil).WithStrictKeywordOnly()
	result, err := tool.Execute(context.Background(), []byte(`{"query":"Cocos","mode":"semantic"}`))
	if err == nil || result.Success || f.hybridCalls != 0 {
		t.Fatal("strict keyword must reject semantic before retrieval")
	}
}

type keywordGuardReranker struct{ rerank.Reranker }

func (*keywordGuardReranker) Rerank(context.Context, string, []string) ([]rerank.RankResult, error) {
	panic("strict grep reached rerank")
}
func TestKeywordGuardRejectsRerankConfiguration(t *testing.T) {
	kb := &types.KnowledgeBase{ID: "fixture-kb", Type: types.KnowledgeBaseTypeDocument}
	kb.IndexingStrategy.KeywordEnabled = true
	f := &keywordGuardKBFixture{kb: kb}
	tool := NewSearchKnowledgeTool(f, nil, nil, types.SearchTargets{&types.SearchTarget{Type: types.SearchTargetTypeKnowledgeBase, KnowledgeBaseID: kb.ID, TenantID: 1}}, &keywordGuardReranker{}, nil).WithStrictKeywordOnly()
	result, err := tool.Execute(context.Background(), []byte(`{"query":"Cocos","mode":"keyword"}`))
	if err == nil || result.Success || f.hybridCalls != 0 {
		t.Fatal("strict keyword must reject rerank configuration before retrieval")
	}
}

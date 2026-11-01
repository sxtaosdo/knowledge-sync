package handler

import (
	"github.com/Tencent/WeKnora/internal/synccanary"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSyncCanaryWriterGateWhileDisabled(t *testing.T) {
	t.Setenv("WEKNORA_SYNC_CANARY_POLICY", "")
	store, err := synccanary.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	h, err := NewSyncCanaryHandler(store)
	if err != nil {
		t.Fatal(err)
	}
	const kb = "00000000-0000-0000-0000-000000000001"
	prefix := "/api/v1/knowledge-bases/" + kb
	scope := types.TenantAPIKeyScope{Name: "shidai3-dev-canary-writer", KnowledgeBaseIDs: types.StringArray{kb},
		Capabilities: types.StringArray{"ingest", "retrieve"}}
	cases := []struct {
		method, path, body string
		code               int
	}{
		{"DELETE", "/api/v1/knowledge/test", "", 403},
		{"POST", prefix + "/knowledge/manual", "{}", 403},
		{"PUT", "/api/v1/knowledge/manual/test", "{}", 403},
		{"POST", prefix + "/hybrid-search", `{"query_text":"canary"}`, 403},
		{"GET", prefix + "/hybrid-search", `{"query_text":"canary"}`, 403},
		{"GET", prefix + "/hybrid-search", `{"disable_vector_match":true,"disable_keywords_match":false}`, 200},
		{"POST", prefix + "/hybrid-search", `{"disable_vector_match":true,"disable_keywords_match":false}`, 200},
		{"POST", prefix + "/sync-canary/operations", "{}", 200},
		{"GET", prefix + "/knowledge", "", 200},
	}
	gin.SetMode(gin.TestMode)
	for _, tc := range cases {
		t.Run(tc.method+tc.path, func(t *testing.T) {
			r := gin.New()
			r.Use(func(c *gin.Context) {
				c.Request = c.Request.WithContext(types.WithTenantAPIKeyScope(c.Request.Context(), scope))
			}, h.Gate())
			r.Handle(tc.method, tc.path, func(c *gin.Context) { c.Status(200) })
			rec := httptest.NewRecorder()
			r.ServeHTTP(rec, httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body)))
			if rec.Code != tc.code {
				t.Fatalf("got %d want %d", rec.Code, tc.code)
			}
		})
	}
	// Disabling the feature never enables its handlers or reserved writers.
	r := gin.New()
	r.GET("/knowledge-bases/:id/sync-canary/capabilities", h.Capabilities)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/knowledge-bases/"+kb+"/sync-canary/capabilities", nil))
	if rec.Code != 503 {
		t.Fatalf("disabled handler returned %d", rec.Code)
	}
	// Removing ingest never restores the stock vector-search route for this key.
	scope.Capabilities = types.StringArray{"retrieve"}
	r = gin.New()
	r.Use(func(c *gin.Context) {
		c.Request = c.Request.WithContext(types.WithTenantAPIKeyScope(c.Request.Context(), scope))
	}, h.Gate())
	r.GET(prefix+"/hybrid-search", func(c *gin.Context) { c.Status(200) })
	rec = httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest("GET", prefix+"/hybrid-search", strings.NewReader(`{"query_text":"canary"}`)))
	if rec.Code != 403 {
		t.Fatalf("retrieve-only writer bypassed keyword gate: %d", rec.Code)
	}
}

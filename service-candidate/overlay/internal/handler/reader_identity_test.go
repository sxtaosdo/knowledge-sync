package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
)

func TestReaderIdentityResponseIsNonsecretAndBounded(t *testing.T) {
	gin.SetMode(gin.TestMode)
	expiry := time.Now().UTC().Add(time.Hour)
	reader := types.TenantAPIKeyScope{KeyID: 2, Name: "private-key-name", ScopeType: types.APIKeyScopeTenant,
		KnowledgeBaseIDs: types.StringArray{"pilot"}, Capabilities: types.StringArray{"retrieve"}, ExpiresAt: &expiry}
	for _, tc := range []struct {
		name   string
		alter  func(*types.TenantAPIKeyScope)
		status int
	}{
		{"valid", func(*types.TenantAPIKeyScope) {}, http.StatusOK},
		{"full", func(s *types.TenantAPIKeyScope) { s.FullAccess = true }, http.StatusForbidden},
		{"platform", func(s *types.TenantAPIKeyScope) { s.ScopeType = types.APIKeyScopePlatform }, http.StatusForbidden},
		{"expired", func(s *types.TenantAPIKeyScope) { v := time.Now().Add(-time.Hour); s.ExpiresAt = &v }, http.StatusUnauthorized},
		{"unbounded", func(s *types.TenantAPIKeyScope) { s.ExpiresAt = nil }, http.StatusForbidden},
	} {
		t.Run(tc.name, func(t *testing.T) {
			scope := reader
			tc.alter(&scope)
			engine := gin.New()
			engine.Use(func(c *gin.Context) {
				ctx := context.WithValue(c.Request.Context(), types.TenantIDContextKey, uint64(1))
				c.Request = c.Request.WithContext(types.WithTenantAPIKeyScope(ctx, scope))
				c.Next()
			})
			engine.GET("/api/v1/auth/reader-identity", (&AuthHandler{}).GetReaderIdentity)
			w := httptest.NewRecorder()
			engine.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/v1/auth/reader-identity", nil))
			if w.Code != tc.status {
				t.Fatalf("status=%d want=%d", w.Code, tc.status)
			}
			if tc.status != http.StatusOK {
				return
			}
			var body struct {
				Success bool                       `json:"success"`
				Data    map[string]json.RawMessage `json:"data"`
			}
			if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if !body.Success || len(body.Data) != 7 || w.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("response projection/cache contract")
			}
			for _, field := range []string{"key_id", "tenant_id", "scope_type", "full_access", "knowledge_base_ids", "capabilities", "expires_at"} {
				if _, ok := body.Data[field]; !ok {
					t.Fatalf("missing %s", field)
				}
			}
			var value string
			json.Unmarshal(body.Data["expires_at"], &value)
			actual, err := time.Parse(time.RFC3339Nano, value)
			if err != nil || !actual.Equal(expiry) {
				t.Fatal("expiry differs from authenticated key")
			}
		})
	}
}

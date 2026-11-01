package router

import (
	"net/http"
	"testing"

	"github.com/Tencent/WeKnora/internal/handler"
	"github.com/gin-gonic/gin"
)

func TestReaderIdentityRouteDeclaresStrictPolicy(t *testing.T) {
	gin.SetMode(gin.TestMode)
	g := &rbacGuards{}
	v1 := gin.New().Group("/api/v1")
	RegisterAuthRoutes(v1, &handler.AuthHandler{}, g)
	p := mustLookupAPIKeyPolicy(t, g, http.MethodGet, "/api/v1/auth/reader-identity")
	if !p.ReaderIdentityOnly {
		t.Fatal("self identity must require scoped reader")
	}
	if mustLookupAPIKeyPolicy(t, g, http.MethodGet, "/api/v1/auth/me").ReaderIdentityOnly {
		t.Fatal("existing auth/me behavior changed")
	}
	if _, ok := g.apiKeyAuthorizer.Lookup(http.MethodGet, "/api/v1/tenants/:id/api-keys"); ok {
		t.Fatal("reader key-management route unexpectedly declared")
	}
}

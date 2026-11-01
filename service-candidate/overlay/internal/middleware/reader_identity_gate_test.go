package middleware

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/gin-gonic/gin"
)

func TestReaderIdentityGateRejectsPrivilegedAndHumanPrincipals(t *testing.T) {
	a := NewAPIKeyRouteAuthorizer()
	const path = "/api/v1/auth/reader-identity"
	a.Register(http.MethodGet, path, APIKeyRoutePolicy{ReaderIdentityOnly: true})
	expiry := time.Now().Add(time.Hour)
	reader := types.TenantAPIKeyScope{KeyID: 2, ScopeType: types.APIKeyScopeTenant,
		KnowledgeBaseIDs: types.StringArray{"pilot"}, Capabilities: types.StringArray{"retrieve"}, ExpiresAt: &expiry}
	full := reader
	full.FullAccess = true
	platform := reader
	platform.ScopeType = types.APIKeyScopePlatform
	writer := reader
	writer.Capabilities = types.StringArray{"retrieve", "ingest"}
	anyKB := reader
	anyKB.KnowledgeBaseIDs = nil
	unbounded := reader
	unbounded.ExpiresAt = nil
	cases := []struct {
		name    string
		scope   *types.TenantAPIKeyScope
		allowed bool
	}{
		{"reader", &reader, true}, {"JWT_owner", nil, false}, {"full_key", &full, false},
		{"platform", &platform, false}, {"writer", &writer, false}, {"any_KB", &anyKB, false},
		{"no_expiry", &unbounded, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := runGate(t, a, tc.scope, http.MethodGet, path); got != tc.allowed {
				t.Fatalf("allowed=%v want=%v", got, tc.allowed)
			}
		})
	}
	if runGate(t, a, &reader, http.MethodGet, "/api/v1/tenants/1/api-keys") {
		t.Fatal("self introspection must not grant key management")
	}
}

type readerFixtureUsers struct{ interfaces.UserService }

func (readerFixtureUsers) GetUserByTenantID(context.Context, uint64) (*types.User, error) {
	return &types.User{ID: "fixture-user", TenantID: 1, IsActive: true}, nil
}

type readerFixtureKeys struct {
	interfaces.TenantAPIKeyService
	key *types.TenantAPIKey
}

func (s readerFixtureKeys) AuthenticateAPIKey(context.Context, string) (*types.TenantAPIKey, error) {
	return s.key, nil
}

func TestReaderIdentityAuthenticatedContextCarriesExpiry(t *testing.T) {
	gin.SetMode(gin.TestMode)
	expiry := time.Now().UTC().Add(time.Hour)
	tenant := uint64(1)
	key := &types.TenantAPIKey{ID: 2, TenantID: &tenant, ScopeType: types.APIKeyScopeTenant,
		KnowledgeBaseIDs: types.StringArray{"pilot"}, Capabilities: types.StringArray{"retrieve"}, ExpiresAt: &expiry}
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodGet, "/api/v1/auth/reader-identity", nil)
	if !authenticateAPIKeyRequest(c, &fakeTenantService{tenant: &types.Tenant{ID: 1}}, readerFixtureUsers{}, readerFixtureKeys{key: key}, "synthetic-not-real") {
		t.Fatal("fixture authentication failed")
	}
	scope, ok := types.TenantAPIKeyScopeFromContext(c.Request.Context())
	if !ok || scope.KeyID != 2 || !scope.IsRestrictedReader() || !scope.ExpiresAt.Equal(expiry) {
		t.Fatal("authenticated key metadata not bound to context")
	}
}

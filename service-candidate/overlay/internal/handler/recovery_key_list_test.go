package handler

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/Tencent/WeKnora/internal/middleware"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/Tencent/WeKnora/internal/utils"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

type recoveryTenantLookup struct{ interfaces.TenantService }

func (recoveryTenantLookup) GetTenantByID(context.Context, uint64) (*types.Tenant, error) {
	return &types.Tenant{ID: 1}, nil
}

type recoveryUserLookup struct{ interfaces.UserService }

func (recoveryUserLookup) GetUserByTenantID(context.Context, uint64) (*types.User, error) {
	return &types.User{ID: "fixture-user", TenantID: 1, IsActive: true}, nil
}

// Exercise SQLite readback, real hash authentication, context projection, the
// strict route gate and the real identity handler. Direct scope fixtures miss
// the StringArray scanner's distinction between SQLite TEXT and BLOB.
func TestRecoveryKeyListIssuedScopeStorageThroughReaderAuth(t *testing.T) {
	t.Setenv("SYSTEM_AES_KEY", "")
	for _, blob := range []bool{false, true} {
		name := "text_is_denied"
		if blob {
			name = "blob_is_authorized"
		}
		t.Run(name, func(t *testing.T) {
			db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
			require.NoError(t, err)
			require.NoError(t, db.AutoMigrate(&types.TenantAPIKey{}))
			token := "sk-synthetic-reader-fixture-only"
			digest := sha256.Sum256([]byte(token))
			expiry := time.Now().UTC().Add(30 * time.Hour).Truncate(time.Microsecond)
			var kbs, caps any = `["fixture-kb"]`, `["retrieve"]`
			if blob {
				kbs, caps = []byte(`["fixture-kb"]`), []byte(`["retrieve"]`)
			}
			require.NoError(t, db.Exec("INSERT INTO tenant_api_keys (id,tenant_id,scope_type,name,key_hash,api_key,full_access,knowledge_base_ids,capabilities,expires_at) VALUES (3,1,'tenant','fixture-reader',?,'',0,?,?,?)", hex.EncodeToString(digest[:]), kbs, caps, expiry.Format(time.RFC3339Nano)).Error)
			apiService := service.NewTenantAPIKeyService(repository.NewTenantAPIKeyRepository(db))
			router := gin.New()
			router.Use(middleware.Auth(recoveryTenantLookup{}, recoveryUserLookup{}, nil, apiService, nil))
			gate := middleware.NewAPIKeyRouteAuthorizer()
			path := "/api/v1/auth/reader-identity"
			gate.Register(http.MethodGet, path, middleware.APIKeyRoutePolicy{ReaderIdentityOnly: true})
			router.Use(gate.Middleware())
			router.GET(path, (&AuthHandler{}).GetReaderIdentity)
			request := httptest.NewRequest(http.MethodGet, path, nil)
			request.Header.Set("X-API-Key", token)
			response := httptest.NewRecorder()
			router.ServeHTTP(response, request)
			if !blob {
				require.Equal(t, http.StatusForbidden, response.Code)
				return
			}
			require.Equal(t, http.StatusOK, response.Code)
			var envelope struct {
				Success bool                       `json:"success"`
				Data    map[string]json.RawMessage `json:"data"`
			}
			require.NoError(t, json.Unmarshal(response.Body.Bytes(), &envelope))
			require.True(t, envelope.Success)
			require.Len(t, envelope.Data, 7)
			require.JSONEq(t, `["fixture-kb"]`, string(envelope.Data["knowledge_base_ids"]))
			require.JSONEq(t, `["retrieve"]`, string(envelope.Data["capabilities"]))
			require.JSONEq(t, `3`, string(envelope.Data["key_id"]))
			require.JSONEq(t, `1`, string(envelope.Data["tenant_id"]))
			require.JSONEq(t, `false`, string(envelope.Data["full_access"]))
			var actualExpiry string
			require.NoError(t, json.Unmarshal(envelope.Data["expires_at"], &actualExpiry))
			parsedExpiry, err := time.Parse(time.RFC3339Nano, actualExpiry)
			require.NoError(t, err)
			require.True(t, parsedExpiry.Equal(expiry))
			require.NotContains(t, response.Body.String(), token)
		})
	}
}

func TestRecoveryKeyListResponseDoesNotExposeUnreadableSecret(t *testing.T) {
	key := &types.TenantAPIKey{ID: 3, Name: "reader", APIKeyState: types.APIKeyPlaintextUnavailable, KeyHash: "synthetic-hash-not-a-real-key", Capabilities: types.StringArray{"retrieve"}}
	encoded, err := json.Marshal(tenantAPIKeyForResponse(key))
	require.NoError(t, err)
	require.Contains(t, string(encoded), `"api_key_state":"unavailable"`)
	require.Contains(t, string(encoded), `"api_key":""`)
	require.NotContains(t, string(encoded), "synthetic-hash")
	require.NotContains(t, string(encoded), "key_hash")
}

func TestRecoveryKeyListHTTP200WithUnavailableAndHashOnlyRows(t *testing.T) {
	t.Setenv("SYSTEM_AES_KEY", "")
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&types.TenantAPIKey{}))
	cipher, err := utils.EncryptAESGCM("sk-fixture-only", []byte("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
	require.NoError(t, err)
	tenant := uint64(1)
	for _, key := range []*types.TenantAPIKey{
		{TenantID: &tenant, Name: "old", APIKey: cipher, KeyHash: "fixture-old"},
		{TenantID: &tenant, Name: "new", APIKey: "", KeyHash: "fixture-new"},
	} {
		require.NoError(t, db.Session(&gorm.Session{SkipHooks: true}).Create(key).Error)
	}
	handler := &TenantHandler{apiKeyService: service.NewTenantAPIKeyService(repository.NewTenantAPIKeyRepository(db))}
	router := gin.New()
	router.GET("/tenants/:id/api-keys", handler.ListAPIKeys)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/tenants/1/api-keys", nil))
	require.Equal(t, http.StatusOK, response.Code)
	var envelope struct {
		Success bool                   `json:"success"`
		Data    []tenantAPIKeyResponse `json:"data"`
	}
	require.NoError(t, json.Unmarshal(response.Body.Bytes(), &envelope))
	require.True(t, envelope.Success)
	require.Len(t, envelope.Data, 2)
	states := map[types.APIKeyPlaintextState]bool{}
	for _, key := range envelope.Data {
		require.Empty(t, key.APIKey)
		states[key.APIKeyState] = true
	}
	require.True(t, states[types.APIKeyPlaintextUnavailable])
	require.True(t, states[types.APIKeyPlaintextNotStored])
	require.NotContains(t, response.Body.String(), cipher)
	require.NotContains(t, response.Body.String(), "fixture-old")
}

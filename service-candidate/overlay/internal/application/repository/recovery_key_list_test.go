package repository

import (
	"context"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/utils"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"testing"
	"time"
)

func TestRecoveryKeyListPreservesUnreadableEncryptedRows(t *testing.T) {
	for _, aesValue := range []string{"", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"} {
		t.Run("missing_or_wrong_"+aesValue, func(t *testing.T) {
			t.Setenv("SYSTEM_AES_KEY", aesValue)
			db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
			require.NoError(t, err)
			require.NoError(t, db.AutoMigrate(&types.TenantAPIKey{}))
			encrypted, err := utils.EncryptAESGCM("sk-synthetic-only", []byte("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
			require.NoError(t, err)
			tenant, foreign := uint64(1), uint64(2)
			expiry := time.Now().UTC().Add(time.Hour)
			for _, key := range []*types.TenantAPIKey{
				{TenantID: &tenant, Name: "unreadable", KeyHash: "fixture-unreadable", APIKey: encrypted, Capabilities: types.StringArray{"retrieve"}, ExpiresAt: &expiry},
				{TenantID: &tenant, Name: "hash-only", KeyHash: "fixture-hash-only", APIKey: ""},
				{TenantID: &foreign, Name: "foreign", KeyHash: "fixture-foreign", APIKey: encrypted},
				{TenantID: &tenant, Name: "revoked", KeyHash: "fixture-revoked", APIKey: encrypted, RevokedAt: &expiry},
			} {
				require.NoError(t, db.Session(&gorm.Session{SkipHooks: true}).Create(key).Error)
			}
			repo := NewTenantAPIKeyRepository(db)
			keys, err := repo.ListAPIKeys(context.Background(), tenant)
			require.NoError(t, err)
			require.Len(t, keys, 2)
			states := map[string]types.APIKeyPlaintextState{}
			for _, key := range keys {
				require.Empty(t, key.APIKey)
				states[key.Name] = key.APIKeyState
			}
			require.Equal(t, types.APIKeyPlaintextUnavailable, states["unreadable"])
			require.Equal(t, types.APIKeyPlaintextNotStored, states["hash-only"])
			var stored string
			require.NoError(t, db.Raw("SELECT api_key FROM tenant_api_keys WHERE name = ?", "unreadable").Scan(&stored).Error)
			require.Equal(t, encrypted, stored)
			var strict types.TenantAPIKey
			require.Error(t, db.Where("name = ?", "unreadable").First(&strict).Error)
			authenticated, err := repo.GetAPIKeyByHash(context.Background(), "fixture-unreadable")
			require.NoError(t, err)
			require.Equal(t, encrypted, authenticated.APIKey)
		})
	}
}

func TestRecoveryKeyListRetainsReadableAndLegacyKeys(t *testing.T) {
	t.Setenv("SYSTEM_AES_KEY", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&types.TenantAPIKey{}))
	encrypted, err := utils.EncryptAESGCM("sk-synthetic-encrypted", utils.GetAESKey())
	require.NoError(t, err)
	tenant := uint64(1)
	for _, key := range []*types.TenantAPIKey{
		{TenantID: &tenant, Name: "encrypted", KeyHash: "fixture-a", APIKey: encrypted},
		{TenantID: &tenant, Name: "legacy", KeyHash: "fixture-b", APIKey: "sk-synthetic-legacy"},
	} {
		require.NoError(t, db.Session(&gorm.Session{SkipHooks: true}).Create(key).Error)
	}
	keys, err := NewTenantAPIKeyRepository(db).ListAPIKeys(context.Background(), tenant)
	require.NoError(t, err)
	require.Len(t, keys, 2)
	for _, key := range keys {
		require.Equal(t, types.APIKeyPlaintextAvailable, key.APIKeyState)
		require.NotContains(t, key.APIKey, "enc:v1:")
	}
}

func TestRecoveryKeyListDoesNotSuppressDatabaseErrors(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	keys, err := NewTenantAPIKeyRepository(db).ListAPIKeys(context.Background(), 1)
	require.Error(t, err)
	require.Nil(t, keys)
}

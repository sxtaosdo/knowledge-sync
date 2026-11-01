package types

import (
	"context"
	"testing"
	"time"
)

func TestReaderIdentityScopeCopiesExpiry(t *testing.T) {
	expiry := time.Now().Add(time.Hour)
	original := TenantAPIKeyScope{KeyID: 2, ScopeType: APIKeyScopeTenant,
		KnowledgeBaseIDs: StringArray{"pilot"}, Capabilities: StringArray{"retrieve"}, ExpiresAt: &expiry}
	ctx := WithTenantAPIKeyScope(context.Background(), original)
	expected := expiry
	expiry = expiry.Add(time.Hour)
	actual, ok := TenantAPIKeyScopeFromContext(ctx)
	if !ok || !actual.ExpiresAt.Equal(expected) || !actual.IsRestrictedReader() {
		t.Fatal("authenticated expiry/grants were not preserved")
	}
	*actual.ExpiresAt = actual.ExpiresAt.Add(time.Hour)
	again, _ := TenantAPIKeyScopeFromContext(ctx)
	if !again.ExpiresAt.Equal(expected) {
		t.Fatal("returned expiry aliases request context")
	}
}

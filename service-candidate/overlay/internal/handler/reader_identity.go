package handler

import (
	"net/http"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
)

// GetReaderIdentity exposes only the authenticated key's nonsecret grants.
// It neither queries key management APIs nor serializes the credential model.
func (h *AuthHandler) GetReaderIdentity(c *gin.Context) {
	ctx := c.Request.Context()
	scope, ok := types.TenantAPIKeyScopeFromContext(ctx)
	tenant, tenantOK := types.TenantIDFromContext(ctx)
	if !ok || !tenantOK || tenant == 0 || !scope.IsRestrictedReader() {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "Scoped reader API key required"})
		return
	}
	if !time.Now().UTC().Before(scope.ExpiresAt.UTC()) {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "Reader key expired"})
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"data": gin.H{
			"key_id":             scope.KeyID,
			"tenant_id":          tenant,
			"scope_type":         scope.ScopeType,
			"full_access":        false,
			"knowledge_base_ids": scope.KnowledgeBaseIDs,
			"capabilities":       scope.Capabilities,
			"expires_at":         scope.ExpiresAt.UTC().Format(time.RFC3339Nano),
		},
	})
}

package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/synccanary"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type SyncCanaryHandler struct{ store *synccanary.Store }

func NewSyncCanaryHandler(store *synccanary.Store) (*SyncCanaryHandler, error) {
	if store.Enabled() {
		if err := store.Resume(); err != nil {
			return nil, err
		}
	}
	return &SyncCanaryHandler{store: store}, nil
}

// First-round canary writers may only POST this immutable operation endpoint.
// The regular API-key gate continues to authorize every read route afterward.
func (h *SyncCanaryHandler) Gate() gin.HandlerFunc {
	return func(c *gin.Context) {
		scope, ok := types.TenantAPIKeyScopeFromContext(c.Request.Context())
		if !ok {
			c.Next()
			return
		}
		matched := scope.Name == "shidai3-dev-canary-writer" || scope.Name == "shidai3-official-canary-writer"
		for _, target := range h.store.Policy().Targets {
			for _, id := range scope.KnowledgeBaseIDs {
				if id == target.KBID {
					matched = true
				}
			}
		}
		if !matched {
			c.Next()
			return
		}
		kb := ""
		if len(scope.KnowledgeBaseIDs) == 1 {
			kb = scope.KnowledgeBaseIDs[0]
		}
		prefix := "/api/v1/knowledge-bases/" + kb
		allowedWrite := scope.HasCapability(types.APIKeyCapabilityIngest) && c.Request.Method == http.MethodPost && c.Request.URL.Path == prefix+"/sync-canary/operations"
		allowedSearch := (c.Request.Method == http.MethodPost || c.Request.Method == http.MethodGet) && c.Request.URL.Path == prefix+"/hybrid-search"
		if scope.FullAccess || scope.IsPlatform() || kb == "" || (!allowedWrite && !allowedSearch && c.Request.Method != http.MethodGet) {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "error": "sync_canary_write_route_denied"})
			return
		}
		for _, cap := range scope.Capabilities {
			if cap != string(types.APIKeyCapabilityIngest) && cap != string(types.APIKeyCapabilityRetrieve) {
				c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "error": "sync_canary_capability_denied"})
				return
			}
		}
		// A canary key cannot request embeddings even through the search endpoint.
		if allowedSearch {
			raw, err := io.ReadAll(io.LimitReader(c.Request.Body, 65537))
			if err != nil || len(raw) > 65536 {
				c.AbortWithStatus(http.StatusBadRequest)
				return
			}
			var params struct {
				DisableVectorMatch   bool `json:"disable_vector_match"`
				DisableKeywordsMatch bool `json:"disable_keywords_match"`
			}
			if json.Unmarshal(raw, &params) != nil || !params.DisableVectorMatch || params.DisableKeywordsMatch {
				c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "error": "sync_canary_keyword_only_required"})
				return
			}
			c.Request.Body = io.NopCloser(strings.NewReader(string(raw)))
		}
		c.Next()
	}
}

func (h *SyncCanaryHandler) target(c *gin.Context, ingest bool) (synccanary.Target, bool) {
	if !h.store.Enabled() {
		c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{"success": false, "error": "sync_canary_disabled"})
		return synccanary.Target{}, false
	}
	scope, ok := types.TenantAPIKeyScopeFromContext(c.Request.Context())
	caller := types.CallerFromContext(c.Request.Context())
	kb := c.Param("id")
	if !ok || scope.FullAccess || scope.IsPlatform() || len(scope.KnowledgeBaseIDs) != 1 || scope.KnowledgeBaseIDs[0] != kb ||
		!scope.HasCapability(types.APIKeyCapabilityRetrieve) || ingest && !scope.HasCapability(types.APIKeyCapabilityIngest) {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "error": "sync_canary_scoped_key_required"})
		return synccanary.Target{}, false
	}
	for _, cap := range scope.Capabilities {
		if cap != string(types.APIKeyCapabilityIngest) && cap != string(types.APIKeyCapabilityRetrieve) {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "error": "sync_canary_capability_denied"})
			return synccanary.Target{}, false
		}
	}
	policy := h.store.Policy()
	if policy.ExpiresAt <= time.Now().Unix() {
		c.AbortWithStatus(http.StatusForbidden)
		return synccanary.Target{}, false
	}
	for _, target := range policy.Targets {
		if target.TenantID == caller.TenantID && target.KBID == kb {
			if ingest && scope.Name != target.Name+"-writer" {
				break
			}
			return target, true
		}
	}
	c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "error": "sync_canary_scope_denied"})
	return synccanary.Target{}, false
}

func (h *SyncCanaryHandler) Capabilities(c *gin.Context) {
	target, ok := h.target(c, true)
	if !ok {
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": gin.H{"enabled": true, "providerId": synccanary.ProviderID,
		"tenantId": target.TenantID, "kbId": target.KBID, "channel": target.Channel, "expiresAt": h.store.Policy().ExpiresAt,
		"documentPaths": []string{"AGENTS.md", "AI_CONTEXT.md"}, "keywordOnly": true, "deleteAllowed": false, "firstAddsOnly": true}})
}

func canaryError(c *gin.Context, err error) {
	code, status := "sync_canary_unavailable", http.StatusServiceUnavailable
	switch {
	case errors.Is(err, gorm.ErrRecordNotFound):
		code, status = "sync_canary_operation_missing", http.StatusNotFound
	case errors.Is(err, synccanary.ErrDenied):
		code, status = "sync_canary_scope_denied", http.StatusForbidden
	case errors.Is(err, synccanary.ErrConflict):
		code, status = "sync_canary_operation_conflict", http.StatusConflict
	case errors.Is(err, synccanary.ErrUnsupported):
		code, status = "sync_canary_first_add_only", http.StatusBadRequest
	}
	c.JSON(status, gin.H{"success": false, "error": code})
}

func (h *SyncCanaryHandler) Accept(c *gin.Context) {
	target, ok := h.target(c, true)
	if !ok {
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 65536)
	decoder := json.NewDecoder(c.Request.Body)
	decoder.DisallowUnknownFields()
	var request synccanary.Request
	if decoder.Decode(&request) != nil || decoder.Decode(new(any)) != io.EOF {
		c.AbortWithStatus(http.StatusBadRequest)
		return
	}
	result, err := h.store.Accept(c.Request.Context(), target.TenantID, target.KBID, request)
	if err != nil {
		canaryError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": result})
}

func (h *SyncCanaryHandler) Observe(c *gin.Context) {
	target, ok := h.target(c, false)
	if !ok {
		return
	}
	result, err := h.store.Observe(c.Request.Context(), target.TenantID, target.KBID, c.Param("operation_id"))
	if err != nil {
		canaryError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": result})
}

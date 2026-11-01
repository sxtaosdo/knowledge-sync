package router

import "github.com/gin-gonic/gin"
import "github.com/Tencent/WeKnora/internal/handler"

func RegisterSyncCanaryRoutes(r *gin.RouterGroup, h *handler.SyncCanaryHandler, g *rbacGuards) {
	if h == nil {
		return
	}
	group := g.apiKeyGroup(r.Group("/knowledge-bases/:id/sync-canary"), apiKeyIngest(apiKeyFullAccess()))
	read := group.With(apiKeyRetrieve(apiKeyFullAccess()))
	group.POST("/operations", g.KBAccessWrite("id"), h.Accept)
	read.GET("/capabilities", g.KBAccessRead("id"), h.Capabilities)
	read.GET("/operations/:operation_id", g.KBAccessRead("id"), h.Observe)
}

package router

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/Tencent/WeKnora/internal/middleware"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestFrontendStaticDoesNotInterceptResourceGrant(t *testing.T) {
	gin.SetMode(gin.TestMode)
	webDir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(webDir, "index.html"), []byte("spa"), 0o600))
	t.Setenv("WEKNORA_WEB_DIR", webDir)

	r := gin.New()
	serveFrontendStatic(r)
	r.GET("/r/:token", func(c *gin.Context) { c.String(http.StatusOK, "resource") })

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/r/token", nil)
	r.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusOK, recorder.Code)
	require.Equal(t, "resource", recorder.Body.String())
}

// Lite SPA fallback must not intercept any native MCP transport method.
func TestFrontendStaticDoesNotInterceptMCPAuth(t *testing.T) {
	gin.SetMode(gin.TestMode)
	webDir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(webDir, "index.html"), []byte("spa"), 0o600))
	t.Setenv("WEKNORA_WEB_DIR", webDir)
	r := gin.New()
	serveFrontendStatic(r)
	for _, method := range []string{http.MethodGet, http.MethodPost, http.MethodDelete} {
		r.Handle(method, "/mcp/:endpoint_id", middleware.MCPEndpointAuth(nil, nil), func(c *gin.Context) { c.String(500, "auth should abort") })
	}
	for _, method := range []string{http.MethodGet, http.MethodPost, http.MethodDelete} {
		recorder := httptest.NewRecorder()
		r.ServeHTTP(recorder, httptest.NewRequest(method, "/mcp/no-endpoint", nil))
		require.Equal(t, http.StatusUnauthorized, recorder.Code, method)
		require.Equal(t, `Bearer realm="weknora-mcp"`, recorder.Header().Get("WWW-Authenticate"), method)
	}
	recorder := httptest.NewRecorder()
	r.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/login", nil))
	require.Equal(t, http.StatusOK, recorder.Code)
	require.Equal(t, "spa", recorder.Body.String())
}

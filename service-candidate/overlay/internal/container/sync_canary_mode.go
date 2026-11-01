package container

import (
	"errors"
	"os"
)

// The approved first trial must not reset or resume historical work. This is
// evaluated before DB initialization; a missing preservation flag fails closed.
func canaryTrialMode() bool { return os.Getenv("WEKNORA_SYNC_CANARY_POLICY") != "" }

func validateCanaryRuntime() error {
	if !canaryTrialMode() {
		return nil
	}
	if os.Getenv("DB_DRIVER") != "sqlite" || os.Getenv("RETRIEVE_DRIVER") != "sqlite" || os.Getenv("AUTO_MIGRATE") != "false" || os.Getenv("REDIS_ADDR") != "" {
		return errors.New("sync_canary requires SQLite DB/retriever, AUTO_MIGRATE=false and no Redis; historical work remains paused")
	}
	return nil
}

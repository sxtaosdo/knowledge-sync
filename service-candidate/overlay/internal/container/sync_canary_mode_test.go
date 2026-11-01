package container

import "testing"

func TestSyncCanaryRuntimeRequiresExplicitPreservation(t *testing.T) {
	t.Setenv("WEKNORA_SYNC_CANARY_POLICY", "")
	t.Setenv("DB_DRIVER", "postgres")
	t.Setenv("AUTO_MIGRATE", "")
	t.Setenv("REDIS_ADDR", "redis")
	if validateCanaryRuntime() != nil || canaryTrialMode() {
		t.Fatal("default runtime changed")
	}
	t.Setenv("WEKNORA_SYNC_CANARY_POLICY", "synthetic-policy.json")
	if validateCanaryRuntime() == nil {
		t.Fatal("unsafe trial startup accepted")
	}
	t.Setenv("DB_DRIVER", "sqlite")
	t.Setenv("RETRIEVE_DRIVER", "sqlite")
	t.Setenv("AUTO_MIGRATE", "false")
	t.Setenv("REDIS_ADDR", "")
	if validateCanaryRuntime() != nil || !canaryTrialMode() {
		t.Fatal("preserved trial rejected")
	}
	for _, tc := range []struct{ key, value string }{{"DB_DRIVER", "postgres"}, {"RETRIEVE_DRIVER", "sqlite,elasticsearch_v8"}, {"AUTO_MIGRATE", "true"}, {"REDIS_ADDR", "redis"}, {"REDIS_ADDR", " \t\n"}} {
		t.Run(tc.key, func(t *testing.T) {
			t.Setenv(tc.key, tc.value)
			if validateCanaryRuntime() == nil {
				t.Fatal("unsafe override accepted")
			}
		})
	}
}

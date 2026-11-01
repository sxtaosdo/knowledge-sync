package sqlite

import (
	"context"
	"fmt"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

// This is a synthetic-only repair rehearsal, not a runtime/admin repair API.
// It uses the official tokenizer and refuses any scope other than five fixed rows.
func TestFixedFiveLocalFTSRepairPlan(t *testing.T) {
	r := newSQLiteRetrieverTestRepository(t)
	require.NoError(t, r.db.Exec("CREATE TABLE chunks(id TEXT,knowledge_id TEXT,knowledge_base_id TEXT,is_enabled INTEGER,index_status TEXT,deleted_at TEXT,chunk_type TEXT)").Error)
	require.NoError(t, r.db.Exec("CREATE TABLE knowledges(id TEXT,knowledge_base_id TEXT,deleted_at TEXT,parse_status TEXT)").Error)
	require.NoError(t, r.db.Exec("INSERT INTO knowledges VALUES('synthetic-doc','synthetic-pilot',NULL,'processing')").Error)
	for i := 0; i < 6; i++ {
		chunk := fmt.Sprintf("synthetic-%d", i)
		require.NoError(t, r.db.Exec("INSERT INTO chunks VALUES(?,'synthetic-doc','synthetic-pilot',1,'ready',NULL,'text')", chunk).Error)
		require.NoError(t, r.Save(context.Background(), &types.IndexInfo{SourceID: chunk, SourceType: types.ChunkSourceType, ChunkID: chunk, KnowledgeID: "synthetic-doc", KnowledgeBaseID: "synthetic-pilot", Content: "Cocos 合成知识测试", IsEnabled: true}, nil))
	}
	var rows []sqliteEmbedding
	require.NoError(t, r.db.Order("id").Find(&rows).Error)
	ids := make([]uint, 5)
	for i := range ids {
		ids[i] = rows[i].ID
	}
	require.NoError(t, r.db.Exec("DELETE FROM lite_embeddings_fts WHERE rowid IN ?", ids).Error)
	require.Error(t, r.ValidateLocalKeywordIndex(context.Background(), "synthetic-pilot"))
	repair := func(fixed []uint, kb string) (int, error) {
		if len(fixed) != 5 {
			return 0, fmt.Errorf("exactly five fixed rows required")
		}
		repaired := 0
		err := r.db.Transaction(func(tx *gorm.DB) error {
			var selected []sqliteEmbedding
			if err := tx.Where("id IN ? AND knowledge_base_id=?", fixed, kb).Order("id").Find(&selected).Error; err != nil {
				return err
			}
			if len(selected) != 5 {
				return fmt.Errorf("fixed row scope changed")
			}
			var present int64
			if err := tx.Raw("SELECT count(*) FROM lite_embeddings_fts WHERE rowid IN ?", fixed).Scan(&present).Error; err != nil {
				return err
			}
			if present == 5 {
				return nil
			} // Repeated completed repair is a no-op.
			if present != 0 {
				return fmt.Errorf("partial/unknown state needs review")
			}
			for _, row := range selected {
				if err := tx.Exec("INSERT INTO lite_embeddings_fts(rowid,content,source_id,chunk_id,knowledge_id,knowledge_base_id) VALUES(?,?,?,?,?,?)", row.ID, tokenizeCJKBigram(row.Content), row.SourceID, row.ChunkID, row.KnowledgeID, row.KnowledgeBaseID).Error; err != nil {
					return err
				}
				repaired++
			}
			return nil
		})
		return repaired, err
	}
	_, err := repair(ids[:4], "synthetic-pilot")
	require.Error(t, err)
	_, err = repair(ids, "foreign-kb")
	require.Error(t, err)
	n, err := repair(ids, "synthetic-pilot")
	require.NoError(t, err)
	require.Equal(t, 5, n)
	require.NoError(t, r.ValidateLocalKeywordIndex(context.Background(), "synthetic-pilot"))
	n, err = repair(ids, "synthetic-pilot")
	require.NoError(t, err)
	require.Zero(t, n)
	var after []sqliteEmbedding
	require.NoError(t, r.db.Order("id").Find(&after).Error)
	require.Equal(t, rows, after, "metadata is unchanged")
	var status string
	require.NoError(t, r.db.Raw("SELECT parse_status FROM knowledges WHERE id='synthetic-doc'").Scan(&status).Error)
	require.Equal(t, "processing", status, "historical processing is not requeued")
	hits, err := r.Retrieve(context.Background(), types.RetrieveParams{Query: "知识", KnowledgeBaseIDs: []string{"synthetic-pilot"}, TopK: 10, RetrieverType: types.KeywordsRetrieverType})
	require.NoError(t, err)
	require.Len(t, hits, 1)
	require.Len(t, hits[0].Results, 6)
}

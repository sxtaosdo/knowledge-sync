from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reader_citations import citation, local_comparison, CitationRejected, KB, DOCUMENT, CHUNK, MAX_CONTENT


def fixture():
    return ({'ok': True, 'data': {'id': DOCUMENT, 'knowledge_base_id': KB, 'title': 'README-P1060.md', 'file_hash': 'a'*32, 'secretExtra': 'MUST_NOT_OUTPUT'}},
            {'ok': True, 'data': {'id': CHUNK, 'knowledge_id': DOCUMENT, 'knowledge_base_id': KB, 'tenant_id': 1, 'chunk_index': 2, 'start_at': 10, 'end_at': 40, 'content': 'P1060 Cocos Creator 正文', 'metadata': {'secretExtra': 'MUST_NOT_OUTPUT'}}},
            {'credentialBindingVerified': True, 'tenantId': 1, 'keyId': 3, 'kbId': KB})


class CitationTests(unittest.TestCase):
    def test_exact_api_content_provenance_locator_and_no_extra_fields(self):
        doc, chunk, binding = fixture()
        result = citation(doc, chunk, binding)
        self.assertEqual(result['citation']['content'], chunk['data']['content'])
        self.assertEqual(result['citation']['contentSHA256'], hashlib.sha256(chunk['data']['content'].encode()).hexdigest())
        self.assertTrue(result['apiPassageReturned'])
        self.assertNotIn('MUST_NOT_OUTPUT', json.dumps(result))

    def test_wrong_document_chunk_parent_or_scope_rejected(self):
        for part, key, value in [(0,'id','foreign'),(0,'knowledge_base_id','foreign'),(0,'title','other.md'),(1,'id','foreign'),(1,'knowledge_id','foreign'),(1,'knowledge_base_id','foreign'),(1,'tenant_id',2)]:
            args=fixture();args[part]['data'][key]=value
            with self.subTest(part=part,key=key), self.assertRaises(CitationRejected):citation(*args)

    def test_omitted_derived_scope_requires_exact_validated_parent(self):
        args=fixture();args[1]['data'].pop('knowledge_base_id');args[1]['data'].pop('tenant_id')
        self.assertEqual(citation(*args)['citation']['knowledgeBaseId'], KB)
        args[0]['data']['knowledge_base_id']='foreign'
        with self.assertRaises(CitationRejected):citation(*args)

    def test_bad_binding_envelope_and_content_rejected(self):
        for value in (False, None, 'true'):
            args=fixture();args[2]['credentialBindingVerified']=value
            with self.assertRaises(CitationRejected):citation(*args)
        for value in ('', ' ', None, 'bad\x00text', 'a'*(MAX_CONTENT+1)):
            args=fixture();args[1]['data']['content']=value
            with self.assertRaises(CitationRejected):citation(*args)
        args=fixture();args[1]['ok']=False
        with self.assertRaises(CitationRejected):citation(*args)

    def test_invalid_locators_are_not_presented_as_citations(self):
        for field,value in [('chunk_index',-1),('start_at',True),('end_at',9),('end_at','40')]:
            args=fixture();args[1]['data'][field]=value
            with self.assertRaises(CitationRejected):citation(*args)

    def test_invalid_scope_types_and_nested_metadata_rejected(self):
        for value in (True, 1.0, '1'):
            args=fixture();args[1]['data']['tenant_id']=value
            with self.assertRaises(CitationRejected):citation(*args)
        args=fixture();args[2]['keyId']=3.0
        with self.assertRaises(CitationRejected):citation(*args)
        args=fixture();args[0]['data']['source']={'secretExtra':'MUST_NOT_OUTPUT'}
        with self.assertRaises(CitationRejected):citation(*args)

    def test_locator_urls_never_expose_userinfo_or_query_credentials(self):
        for prefix, expected in [('https://','https://'), ('HTTPS://','https://'),
                                 ('hTtPs://','https://'), ('  https://','https://'),
                                 ('\tHTTPS://','https://'), ('//','//')]:
            with self.subTest(prefix=prefix):
                args=fixture()
                url=prefix+'user:synthetic-secret@example.com/path.md?token=synthetic-secret#synthetic-secret'
                args[0]['data']['file_path']=url;args[0]['data']['source']=url
                result=citation(*args)
                self.assertEqual(result['document']['file_path'],expected+'example.com/path.md')
                self.assertEqual(result['document']['source'],expected+'example.com/path.md')
                self.assertNotIn('synthetic-secret',json.dumps(result))

    def test_invalid_web_locator_rejected_local_path_preserved(self):
        for value in ('https:missing-host', 'https://example.com:bad/path', 'https://[bad/path'):
            args=fixture();args[0]['data']['source']=value
            with self.subTest(value=value), self.assertRaises(CitationRejected):citation(*args)
        args=fixture();args[0]['data']['file_path']='D:/local/path.md'
        self.assertEqual(citation(*args)['document']['file_path'],'D:/local/path.md')

    def test_local_source_never_replaces_api_passage_or_claims_freshness(self):
        args=fixture();result=citation(*args);api_text=result['citation']['content']
        local_comparison(result,b'new alias redirect',b'new canonical file')
        self.assertEqual(result['citation']['content'],api_text)
        self.assertEqual(result['freshness']['state'],'unknown')
        self.assertFalse(result['localFileComparison'][0]['apiChunkIsExactSubstring'])
        self.assertNotIn('new canonical file',json.dumps(result))


if __name__ == '__main__':unittest.main()

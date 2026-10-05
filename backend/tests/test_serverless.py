"""Request-bound processing must survive retries and cold starts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import backend.app as api
from backend.storage import Store


class Serverless(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = api.store, api.DATA, api.SERVERLESS, api.APP_PASSWORD
        api.DATA = Path(self.temp.name)
        api.store = Store(api.DATA / 'test.sqlite3')
        api.SERVERLESS = True
        api.APP_PASSWORD = ''
        self.client = TestClient(api.app)

    def tearDown(self):
        api.store, api.DATA, api.SERVERLESS, api.APP_PASSWORD = self.previous
        self.temp.cleanup()

    def upload(self):
        result = self.client.post('/api/jobs', files={'file': ('products.csv', b'Product Name,Quantity\nFirst Product,1kg\nSecond Product,2kg', 'text/csv')})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['id']

    def test_batches_restart_and_duplicate_requests(self):
        jid = self.upload()
        with patch.object(api.pool, 'submit') as submit, patch.object(api, 'search_candidates', return_value=[]):
            self.assertEqual(self.client.post(f'/api/jobs/{jid}/start').json()['status'], 'queued')
            submit.assert_not_called()
            first = self.client.post(f'/api/jobs/{jid}/advance').json()
            self.assertEqual(first['processed'], 1)
            self.assertEqual(first['status'], 'queued')
            api.store = Store(api.DATA / 'test.sqlite3')
            second = self.client.post(f'/api/jobs/{jid}/advance').json()
            self.assertEqual(second['processed'], 2)
            self.assertEqual(second['status'], 'complete')
            self.assertEqual(self.client.post(f'/api/jobs/{jid}/advance').json(), second)

    def test_pause_and_ownership(self):
        jid = self.upload()
        self.client.post(f'/api/jobs/{jid}/start')
        self.assertEqual(self.client.post(f'/api/jobs/{jid}/cancel').json()['status'], 'cancelled')
        with patch.object(api, 'search_candidates') as search:
            self.assertEqual(self.client.post(f'/api/jobs/{jid}/advance').json()['processed'], 0)
            search.assert_not_called()
        job = api.store.read(jid)
        job['owner'] = 'different-account'
        api.store.write(job)
        self.assertEqual(self.client.post(f'/api/jobs/{jid}/advance').status_code, 404)

    def test_upload_limit(self):
        self.assertEqual(self.client.post('/api/jobs', files={'file': ('big.csv', b'x' * 4_000_001, 'text/csv')}).status_code, 413)

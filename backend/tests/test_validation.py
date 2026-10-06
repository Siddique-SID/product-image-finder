import unittest
from fastapi.testclient import TestClient
import backend.app as api
from backend import accounts


class ReadableValidation(unittest.TestCase):
    def setUp(self):
        accounts._attempts.clear()
        self.previous = api.APP_PASSWORD
        api.APP_PASSWORD = 'owner-test-password'
        self.client = TestClient(api.app, base_url='https://studio.test')

    def tearDown(self):
        api.APP_PASSWORD = self.previous

    def test_old_invite_request_has_readable_update_instruction(self):
        self.client.post('/api/login', json={'password':api.APP_PASSWORD})
        response = self.client.post('/api/invites')
        self.assertEqual(response.status_code, 422)
        self.assertIsInstance(response.json()['detail'], str)
        self.assertIn('/api/app-update', response.json()['detail'])
        self.assertIn('Invite by email', response.json()['detail'])

    def test_validation_does_not_echo_password(self):
        password='private-password-'*20
        response=self.client.post('/api/login',json={'password':password})
        self.assertEqual(response.status_code,422)
        self.assertIsInstance(response.json()['detail'],str)
        self.assertNotIn(password,response.text)

    def test_update_page_is_public_uncached_and_preserves_session(self):
        response=self.client.get('/api/app-update')
        self.assertEqual(response.status_code,200)
        self.assertIn('Update app',response.text)
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.assertNotIn('localStorage.clear',response.text)
        self.assertNotIn('document.cookie',response.text)
        self.client.post('/api/login',json={'password':api.APP_PASSWORD})
        self.client.get('/api/app-update')
        self.assertTrue(self.client.get('/api/session').json()['authenticated'])

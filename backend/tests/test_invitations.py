import hashlib
import os
import smtplib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from fastapi.testclient import TestClient
import backend.app as api
from backend import accounts
from backend.invitations import invitation_digest, send_invitation
from backend.storage import Store


class EmailInvitations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = api.store, api.APP_PASSWORD
        api.store = Store(Path(self.temp.name) / 'studio.sqlite3')
        api.APP_PASSWORD = 'owner-test-password'
        accounts._attempts.clear()
        self.owner = TestClient(api.app, base_url='https://studio.test')
        self.owner.post('/api/login', json={'password':api.APP_PASSWORD})
        self.client = TestClient(api.app, base_url='https://studio.test')

    def tearDown(self):
        api.store, api.APP_PASSWORD = self.previous
        self.temp.cleanup()

    def signup(self, token, email):
        return self.client.post('/api/register', json={
            'name':'Invited user', 'email':email, 'password':'customer-test-password',
            'access_code':token,
        })

    @patch('backend.app.send_invitation', return_value='sent')
    def test_delivery_binding_and_single_use(self, sender):
        response = self.owner.post('/api/invites', json={'email':'  Guest@Example.test  '})
        self.assertEqual(response.status_code, 200)
        invite = response.json()
        self.assertEqual(invite['delivery'], 'sent')
        self.assertNotIn('code', invite)
        parts = urlsplit(invite['link'])
        self.assertEqual(parts.query, '')
        token = parse_qs(parts.fragment)['invite'][0]
        self.assertEqual(sender.call_args.args, ('guest@example.test', invite['link']))
        self.assertEqual(self.signup(token, 'other@example.test').status_code, 403)
        self.assertEqual(self.signup(token, 'guest@example.test').status_code, 200)
        self.assertEqual(api.store.redeem(invitation_digest(token, 'guest@example.test'), time.time(), 'another', 'another@example.test', 'Other', 'hash', 'hash'), False)
        self.assertEqual(self.client.post('/api/invites', json={'email':'other@example.test'}).status_code, 403)

    def test_expired_and_old_generic_codes_rejected(self):
        api.store.invite(invitation_digest('expired', 'guest@example.test'), time.time()-1)
        api.store.invite(hashlib.sha256(b'old-code').hexdigest(), time.time()+100)
        self.assertEqual(self.signup('expired', 'guest@example.test').status_code, 403)
        self.assertEqual(self.signup('old-code', 'guest@example.test').status_code, 403)
        self.assertEqual(self.client.post('/api/invites', json={'email':'guest@example.test'}).status_code, 401)
        self.assertEqual(self.owner.post('/api/invites', json={'email':'not-an-email'}).status_code, 400)

    @patch.dict(os.environ, {'SMTP_GMAIL_USER':'', 'SMTP_GMAIL_APP_PASSWORD':''})
    def test_unconfigured_email_reports_unsent(self):
        invite = self.owner.post('/api/invites', json={'email':'guest@example.test'}).json()
        self.assertEqual(invite['delivery'], 'not_configured')
        self.assertEqual(send_invitation('guest@example.test', invite['link']), 'not_configured')

    @patch.dict(os.environ, {'SMTP_GMAIL_USER':'owner@gmail.com', 'SMTP_GMAIL_APP_PASSWORD':'test-only-app-password'})
    @patch('backend.invitations.smtplib.SMTP_SSL')
    def test_smtp_tls_sender_and_safe_failure(self, smtp):
        link='https://studio.test/#invite=test-token&email=guest%40example.test'
        self.assertEqual(send_invitation('guest@example.test', link), 'sent')
        smtp.assert_called_once()
        self.assertEqual(smtp.call_args.args, ('smtp.gmail.com',465))
        message=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(message['To'], 'guest@example.test')
        self.assertEqual(message['From'], 'owner@gmail.com')
        self.assertIn(link, message.get_content())
        smtp.side_effect = smtplib.SMTPAuthenticationError(535, b'private provider response')
        self.assertEqual(send_invitation('guest@example.test', link), 'failed')

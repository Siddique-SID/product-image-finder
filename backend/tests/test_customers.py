import unittest, tempfile, hashlib, time, zipfile
from pathlib import Path
from io import BytesIO
from fastapi.testclient import TestClient
from PIL import Image
import backend.app as api
from backend.storage import Store
from backend import accounts

class Customers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = api.store, api.DATA, api.APP_PASSWORD
        api.DATA = Path(self.temp.name)
        api.store = Store(api.DATA / 'test.sqlite3')
        api.APP_PASSWORD = 'owner-test-password'
        accounts._attempts.clear()
        self.owner = TestClient(api.app, base_url='https://studio.test')
        self.assertEqual(self.owner.post('/api/login', json={'password':api.APP_PASSWORD}).status_code,200)
    def tearDown(self):
        api.store, api.DATA, api.APP_PASSWORD = self.old
        self.temp.cleanup()
    def register(self, email):
        client = TestClient(api.app, base_url='https://studio.test')
        code=self.owner.post('/api/invites').json()['code']
        result=client.post('/api/register',json={'name':'Customer', 'email':email,'password':'test-customer-password','access_code':code})
        self.assertEqual(result.status_code,200,result.text)
        return client,result.json()['recovery_code'],code
    def upload(self, client):
        r=client.post('/api/jobs',files={'file':('products.csv',b'Product Name,Quantity\nExample Sauce,700g','text/csv')})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['id']
    def test_customer_isolation_and_image_exports(self):
        a,_,_=self.register('a@example.test'); b,_,_=self.register('b@example.test')
        jid=self.upload(a)
        image=Image.new('RGB',(600,600),'white'); raw=BytesIO(); image.save(raw,'PNG')
        r=a.post(f'/api/jobs/{jid}/products/0/image',files={'file':('image.png',raw.getvalue(),'image/png')})
        self.assertEqual(r.status_code,200,r.text)
        name=r.json()['products'][0]['candidates'][0]['filename']
        self.assertEqual(a.get(f'/api/jobs/{jid}/images/{name}').status_code,200)
        for client in [b,self.owner]:
            self.assertEqual(client.get('/api/jobs').json(),[])
            for path in [f'/api/jobs/{jid}',f'/api/jobs/{jid}/images/{name}',f'/api/jobs/{jid}/export/zip']:
                self.assertEqual(client.get(path).status_code,404)
            self.assertEqual(client.post(f'/api/jobs/{jid}/start').status_code,404)
        a.post(f'/api/jobs/{jid}/products/0',json={'action':'approve'})
        archive=a.get(f'/api/jobs/{jid}/export/zip')
        self.assertEqual(len(zipfile.ZipFile(BytesIO(archive.content)).namelist()),1)
        self.assertIn('approved',a.get(f'/api/jobs/{jid}/export/csv').text)
        self.assertEqual(a.get(f'/api/jobs/{jid}/export/xlsx').status_code,200)
        self.assertEqual(a.get('/api/jobs').headers['cache-control'],'private, no-store')
        self.assertEqual(a.post('/api/invites').status_code,403)
    def test_invitation_and_recovery(self):
        a,recovery,code=self.register('a@example.test')
        b=TestClient(api.app,base_url='https://studio.test')
        self.assertEqual(b.post('/api/register',json={'name':'B','email':'b@example.test','password':'test-customer-password','access_code':code}).status_code,403)
        self.assertEqual(b.post('/api/register',json={'name':'B','email':'b@example.test','password':'test-customer-password','access_code':api.APP_PASSWORD}).status_code,403)
        session_copy=TestClient(api.app,base_url='https://studio.test'); session_copy.cookies.update(a.cookies)
        reset=b.post('/api/recover',json={'email':'a@example.test','password':'changed-test-password','recovery_code':recovery})
        self.assertEqual(reset.status_code,200)
        self.assertEqual(session_copy.get('/api/jobs').status_code,401)
        self.assertEqual(b.post('/api/login',json={'email':'a@example.test','password':'test-customer-password'}).status_code,401)
        self.assertEqual(b.post('/api/login',json={'email':'a@example.test','password':'changed-test-password'}).status_code,200)
        self.assertEqual(b.post('/api/recover',json={'email':'a@example.test','password':'another-test-password','recovery_code':recovery}).status_code,401)
        b.post('/api/logout'); self.assertEqual(b.get('/api/jobs').status_code,401)
    def test_persistence_archive_and_cross_site_protection(self):
        a,_,_=self.register('a@example.test'); jid=self.upload(a)
        self.assertEqual(a.post(f'/api/jobs/{jid}/archive').json()['archived'],True)
        self.assertEqual(a.post(f'/api/jobs/{jid}/start').status_code,409)
        api.store=Store(api.DATA/'test.sqlite3')
        self.assertEqual(a.get(f'/api/jobs/{jid}').status_code,200)
        self.assertEqual(a.post(f'/api/jobs/{jid}/archive').json()['archived'],False)
        self.assertEqual(a.post('/api/logout',headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(a.get('/api/session').json()['authenticated'],True)
    def test_cancel_and_resume(self):
        a,_,_=self.register('a@example.test'); jid=self.upload(a)
        job=api.store.read(jid);job['status']='queued';api.store.write(job)
        self.assertEqual(a.post(f'/api/jobs/{jid}/cancel').json()['status'],'cancelling')
        self.assertEqual(a.post(f'/api/jobs/{jid}/start').status_code,409)
        api.run(jid)
        self.assertEqual(a.get(f'/api/jobs/{jid}').json()['status'],'cancelled')
        self.assertEqual(a.post(f'/api/jobs/{jid}/retry').json()['status'],'ready')
    def test_throttling_and_hashing(self):
        for _ in range(14): self.owner.post('/api/login',json={'password':'wrong'})
        self.assertEqual(self.owner.post('/api/login',json={'password':'wrong'}).status_code,429)
        hashed=accounts.hash_password('example-test-password')
        self.assertNotIn('example-test-password',hashed)
        self.assertTrue(accounts.verify('example-test-password',hashed))

if __name__=='__main__':unittest.main()

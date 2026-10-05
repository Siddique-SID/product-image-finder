import unittest, tempfile, zipfile
from pathlib import Path
from io import BytesIO
from PIL import Image
from fastapi.testclient import TestClient
import backend.app as api
from product_image_finder import Candidate
from backend.storage import Store
from backend import accounts

class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old_store=api.store
        api.store=Store(Path(self.temp.name)/'test.sqlite3')
        accounts._attempts.clear()
    def tearDown(self):
        api.store=self.old_store
        self.temp.cleanup()

    def test_hosted_password_gate(self):
        original=api.APP_PASSWORD
        api.APP_PASSWORD='test-only-password'
        try:
            client=TestClient(api.app,base_url='https://example.test')
            self.assertEqual(client.get('/api/health').status_code,200)
            self.assertEqual(client.get('/api/jobs').status_code,401)
            self.assertEqual(client.post('/api/login',json={'password':'wrong'}).status_code,401)
            self.assertEqual(client.post('/api/login',json={'password':'test-only-password'}).status_code,200)
            self.assertEqual(client.get('/api/jobs').status_code,200)
        finally: api.APP_PASSWORD=original

    def test_catalogue_review_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            original=api.DATA; api.DATA=Path(directory)
            search, download=api.search_candidates, api.download_bytes
            image=Image.new('RGB',(600,600),'white'); raw=BytesIO(); image.save(raw,'PNG')
            api.search_candidates=lambda *args:[Candidate(image_url='https://example.org/image.png',page_url='https://example.org/product',title='Example Sauce 700g')]
            api.download_bytes=lambda _:raw.getvalue()
            try:
                client=TestClient(api.app)
                invalid=client.post('/api/jobs',files={'file':('bad.csv',b'Wrong,Headers\na,b','text/csv')})
                self.assertEqual(invalid.status_code,400)
                response=client.post('/api/jobs',files={'file':('products.csv',b'Product Name,Quantity\nExample Sauce,700g','text/csv')})
                self.assertEqual(response.status_code,200)
                job=response.json(); jid=job['id']
                api.run(jid)
                job=client.get(f'/api/jobs/{jid}').json()
                self.assertEqual(job['status'],'complete'); self.assertEqual(job['products'][0]['status'],'review')
                self.assertEqual(client.post(f'/api/jobs/{jid}/start').status_code,409)
                response=client.post(f'/api/jobs/{jid}/products/0',json={'action':'approve'})
                self.assertEqual(response.json()['products'][0]['status'],'approved')
                archive=client.get(f'/api/jobs/{jid}/export/zip')
                self.assertEqual(len(zipfile.ZipFile(BytesIO(archive.content)).namelist()),1)
                self.assertIn('approved',client.get(f'/api/jobs/{jid}/export/csv').text)
                self.assertEqual(client.get(f'/api/jobs/{jid}/export/xlsx').status_code,200)
                self.assertEqual(client.post(f'/api/jobs/{jid}/products/0',json={'action':'select','candidate':99}).status_code,400)
                self.assertEqual(client.post(f'/api/jobs/{jid}/products/0/image',files={'file':('new.png',raw.getvalue(),'image/png')}).status_code,200)
                self.assertEqual(api.read(jid)['products'][0]['status'],'review')
            finally: api.DATA=original; api.search_candidates=search; api.download_bytes=download

if __name__=='__main__': unittest.main()

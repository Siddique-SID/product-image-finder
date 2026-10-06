import unittest
from unittest.mock import patch, Mock
import requests
from product_image_finder import catalogue_search, search_candidates, Candidate, SearchUnavailable, pack_sizes


class ImageSearch(unittest.TestCase):
    def test_recommendations_are_filtered_and_wrong_pack_sizes_warn(self):
        response = Mock()
        response.json.return_value = {'resources': {'results': {'products': [
            {'title': 'SOFRA BARBECUE SAUCE 500G', 'image': '//cdn.shopify.com/sauce.png', 'url': '/products/sauce'},
            {'title': 'SOFRA HOT CHILLI SAUCE 500G', 'image': 'https://example.org/chilli.png'},
        ]}}}
        with patch('product_image_finder.requests.get', return_value=response):
            results = catalogue_search('https://example.org', 'Sofra Barbecue Sauce', '700g')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].image_url, 'https://cdn.shopify.com/sauce.png')
        self.assertIn('Pack size differs', results[0].reason)
        self.assertEqual(pack_sizes('1L'), pack_sizes('1000 ml'))

    def test_one_blocked_source_does_not_discard_working_source(self):
        candidate = Candidate('https://example.org/image.jpg', title='Shan Biryani Masala 50g')
        def provider(source, *_):
            if 'kwfood' in source: return [candidate]
            raise requests.HTTPError('403')
        with patch('product_image_finder.catalogue_search', side_effect=provider), patch('product_image_finder.ddg_search') as ddg:
            self.assertEqual(search_candidates('Shan Biryani Masala', '50g'), [candidate])
            ddg.assert_not_called()

    def test_blocked_search_is_reported_instead_of_empty_success(self):
        with patch('product_image_finder.catalogue_search', return_value=[]), patch('product_image_finder.ddg_search', side_effect=requests.HTTPError('403')):
            with self.assertRaisesRegex(SearchUnavailable, 'web image search is blocked'):
                search_candidates('Unknown product', '1L')


if __name__ == '__main__': unittest.main()

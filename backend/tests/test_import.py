import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
from product_image_finder import load_products


class CatalogueImport(unittest.TestCase):
    def test_excel_title_blank_rows_and_cover_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalogue.xlsx'
            book = Workbook()
            book.active.append(['Catalogue instructions'])
            sheet = book.create_sheet('Grocery Products')
            for row in [
                ['Grocery Product List'], [],
                ['Product Name', 'Brand Name', 'Quantity'],
                ['Whole Organic Milk', 'Horizon Organic', '1 Gallon'],
                ['Sparkling Water', 'Example Brand', '1 Liter (Pack of 6)'],
            ]:
                sheet.append(row)
            book.save(path)
            result = load_products(path)
            self.assertEqual(result.to_dict('records'), [
                {'Product Name': 'Whole Organic Milk', 'Quantity': '1 Gallon'},
                {'Product Name': 'Sparkling Water', 'Quantity': '1 Liter (Pack of 6)'},
            ])

    def test_csv_title_short_records_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalogue.csv'
            path.write_text('Grocery Product List\n\nSKU,Item Description,Pack\n123,Olive Oil,750 ml\n')
            self.assertEqual(load_products(path).to_dict('records'), [
                {'Product Name': 'Olive Oil', 'Quantity': '750 ml'},
            ])

    def test_standard_csv_preserves_deduplication_and_missing_quantity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalogue.csv'
            path.write_text('Product Title,Quantity / Pack Size\nSauce,700g\nSauce,700g\nMilk,\n,1L\n')
            self.assertEqual(load_products(path).to_dict('records'), [
                {'Product Name': 'Sauce', 'Quantity': '700g'},
                {'Product Name': 'Milk', 'Quantity': ''},
            ])

    def test_no_table_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalogue.csv'
            path.write_text('Wrong,Headers\na,b\n')
            with self.assertRaisesRegex(ValueError, 'Could not identify a product table'):
                load_products(path)
